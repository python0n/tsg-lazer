"""API v2 module - osu! lazer compatible endpoints."""

from fastapi import APIRouter

from app.api.v2 import admin
from app.api.v2 import beatmaps
from app.api.v2 import password_reset
from app.api.v2 import blocks
from app.api.v2 import chat
from app.api.v2 import friends
from app.api.v2 import me
from app.api.v2 import notifications
from app.api.v2 import oauth
from app.api.v2 import rankings
from app.api.v2 import register
from app.api.v2 import rooms
from app.api.v2 import scores
from app.api.v2 import server
from app.api.v2 import tags
from app.api.v2 import users

router = APIRouter(prefix="/api/v2")

router.include_router(oauth.router, tags=["OAuth"])
router.include_router(register.router, tags=["Registration"])
router.include_router(me.router, tags=["Me"])
router.include_router(users.router, tags=["Users"])
router.include_router(admin.router, tags=["Admin"])
router.include_router(password_reset.router)
router.include_router(beatmaps.router, tags=["Beatmaps"])
router.include_router(scores.router, tags=["Scores"])
router.include_router(rankings.router, tags=["Rankings"])
router.include_router(server.router, tags=["Server"])
router.include_router(rooms.router, tags=["Multiplayer"])
router.include_router(chat.router, tags=["Chat"])
router.include_router(notifications.router, tags=["Notifications"])
router.include_router(friends.router, tags=["Friends"])
router.include_router(blocks.router, tags=["Blocks"])
router.include_router(tags.router, tags=["Tags"])
