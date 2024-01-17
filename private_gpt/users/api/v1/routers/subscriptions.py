from typing import Any, List
from private_gpt.users import crud, models, schemas
from private_gpt.users.api import deps
from private_gpt.users.constants.role import Role
from fastapi import APIRouter, Body, Depends, HTTPException, Security,status
from fastapi.encoders import jsonable_encoder
from pydantic.networks import EmailStr
from sqlalchemy.orm import Session
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


@router.post("/create", response_model=schemas.Subscription)
def create_subscription(
    subscription_in: schemas.SubscriptionCreate,
    db: Session = Depends(deps.get_db),
) -> Any:
    """
    Create a new subscription
    """
    existing_subscription = crud.subscription.get_by_company_id(db, company_id=subscription_in.company_id)
    if existing_subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Company is already subscribed to a plan.",
        )

    subscription = crud.subscription.create(db=db, obj_in=subscription_in)
    subscription_dict = jsonable_encoder(subscription)

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "message": "Subscription created successfully", 
            "subscription": subscription_dict
        },
    )


@router.get("/{subscription_id}", response_model=schemas.Subscription)
def read_subscription(
    subscription_id: int,
    db: Session = Depends(deps.get_db),
):
    subscription = crud.subscription.get_by_id(db, subscription_id=subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    subscription_dict = jsonable_encoder(subscription)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Subscription retrieved successfully", 
            "subscription": subscription_dict
        },
    )


@router.get("/company/{company_id}", response_model=List[schemas.Subscription])
def read_subscriptions_by_company(
    company_id: int,
    db: Session = Depends(deps.get_db),
):
    subscriptions = crud.subscription.get_by_company_id(db, company_id=company_id)
    subscriptions_list = [jsonable_encoder(subscription) for subscription in subscriptions]

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Subscriptions retrieved successfully", 
            "subscriptions": subscriptions_list
        },
    )


@router.put("/{subscription_id}", response_model=schemas.Subscription)
def update_subscription(
    subscription_id: int, 
    subscription_in: schemas.SubscriptionUpdate,
    db: Session = Depends(deps.get_db),
):
    subscription = crud.subscription.get_by_id(db, subscription_id=subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if not subscription.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company subscription is not active. Please contact support for assistance.",
        )

    updated_subscription = crud.subscription.update(db=db, db_obj=subscription, obj_in=subscription_in)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": "Subscription updated successfully", 
            "subscription": jsonable_encoder(updated_subscription)
        },
    )


@router.delete("/{subscription_id}")
def delete_subscription(
    subscription_id: int,
    db: Session = Depends(deps.get_db),
):
    subscription = crud.subscription.remove(db=db, id=subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return JSONResponse(
        content={"message": "Subscription deleted successfully"}, 
        status_code=status.HTTP_200_OK
    )