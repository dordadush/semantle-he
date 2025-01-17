#!/usr/bin/env python
from typing import Union

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import status
from fastapi.responses import Response

from common import schemas
from logic.game_logic import EasterEggLogic
from logic.user_logic import UserHistoryLogic
from logic.user_logic import UserClueLogic
from routers.base import get_date
from routers.base import get_logics

game_router = APIRouter()


@game_router.get("/api/distance")
async def distance(
        request: Request,
        word: str = Query(default=..., min_length=2, max_length=24, regex=r"^[א-ת ']+$"),
) -> Union[schemas.DistanceResponse, list[schemas.DistanceResponse]]:
    word = word.replace("'", "")
    if egg := EasterEggLogic.get_easter_egg(word):
        response = schemas.DistanceResponse(
            guess=word, similarity=99.99, distance=-1, egg=egg
        )
    else:
        logic, cache_logic = get_logics(app=request.app)
        sim = await logic.get_similarity(word)
        cache_score = await cache_logic.get_cache_score(word)
        if cache_score == 1000:
            solver_count = await logic.get_and_update_solver_count()
        else:
            solver_count = None
        response = schemas.DistanceResponse(
            guess=word,
            similarity=sim,
            distance=cache_score,
            solver_count=solver_count,
        )
    if request.headers.get("x-sh-version", "2022-02-20") >= "2023-09-10":
        if request.state.user:
            history_logic = UserHistoryLogic(
                request.app.state.mongo,
                request.state.user,
                get_date(request.app.state.days_delta)
            )
            return await history_logic.update_and_get_history(response)
        else:
            return [response]
    return response

@game_router.get("/api/clue")
async def get_clue(request: Request):
    if not request.state.user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    else:
        logic, _ = get_logics(app=request.app)
        user_logic = UserClueLogic(
            mongo=request.app.state.mongo,
            user=request.state.user,
            secret=await logic.secret_logic.get_secret(),
            date=get_date(request.app.state.days_delta),
        )
        try:
            clue = await user_logic.get_clue()
        except ValueError:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED)
        if clue is None:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        else:
            return {"clue": clue}
