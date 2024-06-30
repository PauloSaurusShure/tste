import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, AnyStr, BinaryIO, Sequence, Any, List

from injector import inject, singleton
from llama_index.core.node_parser import SemanticSplitterNodeParser, SentenceSplitter, SentenceWindowNodeParser
from llama_index.core.storage import StorageContext
from llama_index.core.schema import BaseNode , ObjectType , TextNode

from private_gpt.components.embedding.embedding_component import EmbeddingComponent
from private_gpt.components.ingest.ingest_component import get_ingestion_component
from private_gpt.components.llm.llm_component import LLMComponent
from private_gpt.components.node_store.node_store_component import NodeStoreComponent
from private_gpt.components.vector_store.vector_store_component import (
    VectorStoreComponent,
)
from private_gpt.server.ingest.model import IngestedDoc
from private_gpt.settings.settings import settings


from llama_index.core.extractors import (
    SummaryExtractor
)
if TYPE_CHECKING:
    from llama_index.core.storage.docstore.types import RefDocInfo

logger = logging.getLogger(__name__)

from langchain.text_splitter import MarkdownTextSplitter
from llama_index.core.node_parser import LangchainNodeParser

DEFAULT_CHUNK_SIZE = 384
SENTENCE_CHUNK_OVERLAP = 50

class SafeSemanticSplitter(SemanticSplitterNodeParser):

    safety_chunker: SentenceSplitter = SentenceSplitter(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=SENTENCE_CHUNK_OVERLAP)

    def _parse_nodes(
            self, 
            nodes, 
            show_progress: bool = False, 
            **kwargs
    ) -> List[BaseNode]:
        all_nodes: List[BaseNode] = super()._parse_nodes(nodes=nodes, show_progress=show_progress, **kwargs)
        all_good = True
        for node in all_nodes:
            if node.get_type() == ObjectType.TEXT:
                node: TextNode= node
                if self.safety_chunker._token_size(node.text) > self.safety_chunker.chunk_size:
                    logging.info("Chunk size too big after semantic chunking: switching to static chunking")
                    all_good = False
                    break
            if not all_good:
                all_nodes = self.safety_chunker._parse_nodes(nodes, show_progress=show_progress, **kwargs)
        return all_nodes


@singleton
class IngestService:
    @inject
    def __init__(
        self,
        llm_component: LLMComponent,
        vector_store_component: VectorStoreComponent,
        embedding_component: EmbeddingComponent,
        node_store_component: NodeStoreComponent,
    ) -> None:
        self.llm_service = llm_component
        self.storage_context = StorageContext.from_defaults(
            vector_store=vector_store_component.vector_store,
            docstore=node_store_component.doc_store,
            index_store=node_store_component.index_store,
        )       
        # parser = LangchainNodeParser(MarkdownTextSplitter(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=SENTENCE_CHUNK_OVERLAP))
        # nodes = parser.get_nodes_from_documents(nodes)
        node_parser = SafeSemanticSplitter.from_defaults(
            embed_model=embedding_component.embedding_model,
            breakpoint_percentile_threshold=95,
            include_metadata=True,
            include_prev_next_rel=True,
        )
        # node_parser =  SentenceWindowNodeParser.from_defaults(
        #     window_size=3,
        #     window_metadata_key="window",
        #     original_text_metadata_key="original_text",
        # )
        self.ingest_component = get_ingestion_component(
            self.storage_context,
            embed_model=embedding_component.embedding_model,
            transformations=[
                node_parser,
                embedding_component.embedding_model,
                # SummaryExtractor(llm=self.llm_service.llm)
            ],
            settings=settings(),
        )

    def _ingest_data(self, file_name: str, file_data: AnyStr) -> list[IngestedDoc]:
        logger.debug("Got file data of size=%s to ingest", len(file_data))
        # llama-index mainly supports reading from files, so
        # we have to create a tmp file to read for it to work
        # delete=False to avoid a Windows 11 permission error.
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            try:
                path_to_tmp = Path(tmp.name)
                if isinstance(file_data, bytes):
                    path_to_tmp.write_bytes(file_data)
                else:
                    path_to_tmp.write_text(str(file_data))
                return self.ingest_file(file_name, path_to_tmp)
            finally:
                tmp.close()
                path_to_tmp.unlink()

    def ingest_file(self, file_name: str, file_data: Path) -> list[IngestedDoc]:
        logger.info("Ingesting file_name=%s", file_name)
        documents = self.ingest_component.ingest(file_name, file_data)
        logger.info("Finished ingestion file_name=%s", file_name)
        return [IngestedDoc.from_document(document) for document in documents]

    def ingest_text(self, file_name: str, text: str) -> list[IngestedDoc]:
        logger.debug("Ingesting text data with file_name=%s", file_name)
        return self._ingest_data(file_name, text)

    async def ingest_bin_data(
        self, file_name: str, raw_file_data: BinaryIO
    ) -> list[IngestedDoc]:
        logger.debug("Ingesting binary data with file_name=%s", file_name)
        file_data = raw_file_data.read()
        return self._ingest_data(file_name, file_data)

    def bulk_ingest(self, files: list[tuple[str, Path]]) -> list[IngestedDoc]:
        logger.info("Ingesting file_names=%s", [f[0] for f in files])
        documents = self.ingest_component.bulk_ingest(files)
        logger.info("Finished ingestion file_name=%s", [f[0] for f in files])
        return [IngestedDoc.from_document(document) for document in documents]

    def list_ingested(self) -> list[IngestedDoc]:
        ingested_docs: list[IngestedDoc] = []
        try:
            docstore = self.storage_context.docstore
            ref_docs: dict[str, RefDocInfo] | None = docstore.get_all_ref_doc_info()

            if not ref_docs:
                return ingested_docs

            for doc_id, ref_doc_info in ref_docs.items():
                doc_metadata = None
                if ref_doc_info is not None and ref_doc_info.metadata is not None:
                    doc_metadata = IngestedDoc.curate_metadata(ref_doc_info.metadata)
                ingested_docs.append(
                    IngestedDoc(
                        object="ingest.document",
                        doc_id=doc_id,
                        doc_metadata=doc_metadata,
                    )
                )
        except ValueError:
            logger.warning("Got an exception when getting list of docs", exc_info=True)
            pass
        logger.debug("Found count=%s ingested documents", len(ingested_docs))
        return ingested_docs

    def delete(self, doc_id: str) -> None:
        """Delete an ingested document.

        :raises ValueError: if the document does not exist
        """
        logger.info(
            "Deleting the ingested document=%s in the doc and index store", doc_id
        )
        self.ingest_component.delete(doc_id)

    def get_doc_ids_by_filename(self, filename: str) -> list[str]:
        doc_ids: set[str] = set()
        try:
            docstore = self.storage_context.docstore
            for node in docstore.docs.values():
                if node.metadata is not None and node.metadata.get("file_name") == filename:
                    doc_ids.add(node.ref_doc_id)

        except ValueError:
            logger.warning(
                "Got an exception when getting doc_ids by filename", exc_info=True)
            pass

        logger.debug("Found count=%s doc_ids for filename '%s'",
                     len(doc_ids), filename)
        return doc_ids
