import json

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

router = APIRouter(prefix="/relay", tags=["relay"])


@router.websocket("/ws")
async def relay_ws(websocket: WebSocket):
    hub = websocket.app.state.relay_hub
    await websocket.accept()
    await hub.register(websocket)
    try:
        while True:
            # Agent sends RelayResponse JSON
            data = await websocket.receive_text()
            hub.fulfill(json.loads(data))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(websocket)
