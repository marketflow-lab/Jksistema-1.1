"""Status of the current user's cloud connection, with no credential projection."""
from fastapi import APIRouter, Header, Response

from backend.services import firebase_user_session


def create_firebase_user_session_router(authorize):
    router = APIRouter(tags=["firebase-session"])

    @router.get("/api/firebase/session/status")
    def session_status(response: Response, authorization: str | None = Header(default=None)):
        response.headers["Cache-Control"] = "no-store"
        identity = authorize(authorization)
        session = firebase_user_session.current(identity["client_id"])
        if session is not None:
            return session.status()
        return {"success": True, "configured": False, "ready": False,
                "source": "none", "code": "server_setup_required",
                "restart_required": False, "can_migrate": False,
                "replacement_required": False}

    return router
