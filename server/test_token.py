# test_token.py
import base64, json, os
from dotenv import load_dotenv
from livekit import api

load_dotenv()

token = (
    api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
    .with_identity("test")
    .with_grants(api.VideoGrants(room_join=True, room="test-room"))
    .with_room_config(
        api.RoomConfiguration(agents=[api.RoomAgentDispatch(agent_name="aria")])
    )
    .to_jwt()
)

payload = token.split(".")[1]
payload += "=" * (-len(payload) % 4)
print(json.dumps(json.loads(base64.urlsafe_b64decode(payload)), indent=2))