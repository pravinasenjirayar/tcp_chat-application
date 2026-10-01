"""Run the TCP chat application's web server and local TCP message relay.

The browser sends requests over HTTP and receives live updates over SSE.
Chat messages travel through the TCP relay before they are saved and sent
to each connected browser.

Start the application from this folder with ``python app.py``.
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Server addresses and timing settings.
ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
# Listen on every interface so Render's proxy can reach the HTTP server.
HTTP_HOST = "0.0.0.0"
# Render sets PORT; use 8000 when running locally.
HTTP_PORT = int(os.environ.get("PORT", "8000"))
TCP_HOST = "127.0.0.1"
TCP_PORT = 5001
TCP_BACKLOG = 25
TCP_TIMEOUT_SECONDS = 1.5
TCP_SHUTDOWN_TIMEOUT_SECONDS = 1
SSE_HEARTBEAT_SECONDS = 30
MAX_MESSAGES_PER_ROOM = 200
DEFAULT_ROOM = "General"

# Room names are also used by the room selector in the browser.
ROOMS = {
    "General": "General discussion room",
    "Technology": "Technology and innovation updates",
    "Programming": "Code, bugs, and project discussions",
    "Random": "Casual conversations and fun updates",
}


class ChatState:
    """Store chat sessions, room messages, and live SSE connections."""

    def __init__(self):
        self.sessions = {}
        self.messages = {room: [] for room in ROOMS}
        self.subscribers = {}
        # HTTP handlers and the TCP relay access this shared state on threads.
        self.lock = threading.RLock()

    def add_system_message(self, room_name: str, text: str):
        """Add a join/leave notice to a room's message history."""
        with self.lock:
            self.messages.setdefault(room_name, []).append(
                {
                    "id": uuid.uuid4().hex[:8],
                    "username": "System",
                    "displayName": "System",
                    "text": text,
                    "timestamp": datetime.now().strftime("%H:%M"),
                    "system": True,
                }
            )
            self.trim_room(room_name)

    def trim_room(
        self,
        room_name: str,
        max_messages: int = MAX_MESSAGES_PER_ROOM,
    ):
        """Keep recent room history from growing without a limit."""
        with self.lock:
            room_messages = self.messages.get(room_name, [])
            if len(room_messages) > max_messages:
                self.messages[room_name] = room_messages[-max_messages:]

    def subscribe(self, session_id: str, writer):
        """Register the response stream used to send updates to a browser."""
        with self.lock:
            if session_id not in self.sessions:
                return False
            self.subscribers.setdefault(session_id, []).append(writer)
            return True

    def unsubscribe(self, session_id: str, writer):
        """Remove a closed browser stream from the subscriber list."""
        with self.lock:
            writers = self.subscribers.get(session_id)
            if writers is None:
                return
            try:
                writers.remove(writer)
            except ValueError:
                return
            if not writers:
                self.subscribers.pop(session_id, None)

    def notify_subscribers(self, session_id: str, event_type: str, payload):
        """Send one SSE event to every open stream for a session."""
        with self.lock:
            if session_id in self.sessions:
                writers = list(self.subscribers.get(session_id, []))
            else:
                writers = []

        event = {
            "type": event_type,
            "payload": payload,
            "sessionId": session_id,
        }
        message = f"data: {json.dumps(event)}\n\n".encode("utf-8")
        for writer in writers:
            try:
                writer.write(message)
                writer.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                self.unsubscribe(session_id, writer)

    def broadcast_state(self):
        """Send each connected session an updated snapshot of its room."""
        with self.lock:
            session_ids = list(self.sessions)

        for session_id in session_ids:
            payload = self.snapshot_for(session_id)
            self.notify_subscribers(session_id, "state", payload)

    def snapshot_for(self, session_id: str):
        """Build the JSON-ready state shown to one session."""
        with self.lock:
            session = self.sessions.get(session_id)
            current_room = session.get("room") if session else DEFAULT_ROOM
            current_user = session if session else None

            users = []
            for sid, data in self.sessions.items():
                users.append(
                    {
                        "id": sid,
                        "username": data["username"],
                        "displayName": data.get("displayName") or data["username"],
                        "room": data["room"],
                        "status": "online",
                    }
                )

            room_cards = []
            for room_name, description in ROOMS.items():
                room_cards.append(
                    {
                        "name": room_name,
                        "description": description,
                        "memberCount": sum(
                            1
                            for current in self.sessions.values()
                            if current["room"] == room_name
                        ),
                    }
                )

            profile = {
                "username": current_user["username"] if current_user else "",
                "displayName": current_user.get("displayName") if current_user else "",
                "room": current_room,
                "status": "online",
            }

            return {
                "rooms": room_cards,
                "users": users,
                "messages": list(self.messages.get(current_room, [])),
                "currentRoom": current_room,
                "currentUser": profile,
                "welcome": current_user["username"] if current_user else "",
            }

    def search(self, query: str):
        """Search room names, users, and recent messages."""
        normalized = query.strip().lower()
        if not normalized:
            return {"rooms": [], "users": [], "messages": []}

        with self.lock:
            rooms = [
                {"name": name, "description": desc}
                for name, desc in ROOMS.items()
                if normalized in name.lower() or normalized in desc.lower()
            ]
            users = [
                {
                    "username": session["username"],
                    "displayName": session.get("displayName") or session["username"],
                }
                for session in self.sessions.values()
                if normalized in session["username"].lower()
                or normalized in (session.get("displayName") or "").lower()
            ]
            messages = []
            for room_name, entries in self.messages.items():
                for entry in entries:
                    text = (entry.get("text") or "").lower()
                    if normalized in text:
                        messages.append({
                            "room": room_name,
                            "username": entry.get("username", "Unknown"),
                            "text": entry.get("text", ""),
                            "timestamp": entry.get("timestamp", ""),
                        })
            return {"rooms": rooms, "users": users, "messages": messages[:20]}

    def login(self, username: str, display_name: str):
        """Create a unique username and return its new session ID."""
        cleaned_username = username.strip()
        if not cleaned_username:
            raise ValueError("Username is required.")

        cleaned_name = (display_name or cleaned_username).strip() or cleaned_username
        session_id = uuid.uuid4().hex
        room_name = DEFAULT_ROOM
        with self.lock:
            unique_username = cleaned_username
            suffix = 1
            while any(
                session["username"].lower() == unique_username.lower()
                for session in self.sessions.values()
            ):
                unique_username = f"{cleaned_username}{suffix}"
                suffix += 1
            self.sessions[session_id] = {
                "username": unique_username,
                "displayName": cleaned_name,
                "room": room_name,
                "joinedAt": datetime.now().strftime("%H:%M"),
            }
            self.add_system_message(room_name, f"{unique_username} joined the chat")
        self.broadcast_state()
        return session_id

    def logout(self, session_id: str):
        """Remove a session and announce its departure."""
        with self.lock:
            session = self.sessions.pop(session_id, None)
            if not session:
                return
            room_name = session["room"]
            self.add_system_message(room_name, f"{session['username']} left the chat")
        self.broadcast_state()

    def switch_room(self, session_id: str, room_name: str):
        """Move a session to a valid room and notify connected browsers."""
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                return
            if room_name not in ROOMS:
                room_name = DEFAULT_ROOM
            previous_room = session["room"]
            session["room"] = room_name
            self.add_system_message(room_name, f"{session['username']} joined {room_name}")
            self.messages.setdefault(room_name, [])
            if previous_room != room_name:
                self.add_system_message(
                    previous_room,
                    f"{session['username']} left {previous_room}",
                )
        self.broadcast_state()

    def send_message(self, session_id: str, text: str, username: str, room_name: str):
        """Validate a sender, save their message, and broadcast room state."""
        with self.lock:
            session = self.sessions.get(session_id)
            if (
                not session
                or not isinstance(text, str)
                or session["username"] != username
                or session["room"] != room_name
            ):
                return None
            message_text = text.strip()
            if not message_text:
                return None

            message = {
                "id": uuid.uuid4().hex[:8],
                "username": session["username"],
                "displayName": session.get("displayName") or session["username"],
                "text": message_text,
                "room": room_name,
                "timestamp": datetime.now().strftime("%H:%M"),
                "system": False,
            }
            self.messages.setdefault(room_name, []).append(message)
            self.trim_room(room_name)
        self.broadcast_state()
        return message


STATE = ChatState()


class TCPChatServer:
    """Accept newline-delimited JSON events from the HTTP-to-TCP bridge."""

    def __init__(self):
        self.sockets = []
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind((TCP_HOST, TCP_PORT))
        self.server.listen(TCP_BACKLOG)
        self.running = True
        self.lock = threading.Lock()

    def start(self):
        """Start accepting TCP connections on a background thread."""
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        """Accept connections and give each one its own handler thread."""
        while self.running:
            try:
                conn, _ = self.server.accept()
            except OSError:
                break

            with self.lock:
                self.sockets.append(conn)
            threading.Thread(target=self.handle_client, args=(conn,), daemon=True).start()

    def handle_client(self, conn):
        """Read TCP JSON lines, handle messages, and return acknowledgements."""
        try:
            with conn.makefile("rb") as reader:
                while self.running:
                    line = reader.readline()
                    if not line:
                        break
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        response = {"success": False, "message": "Invalid TCP JSON message."}
                        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                        continue

                    if not isinstance(event, dict):
                        response = {"success": False, "message": "Invalid TCP event."}
                        conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                        continue

                    event_type = event.get("type")
                    payload = event.get("payload")
                    if event_type == "message" and isinstance(payload, dict):
                        message = STATE.send_message(
                            payload.get("sessionId", ""),
                            payload.get("text", ""),
                            payload.get("username", ""),
                            payload.get("room", ""),
                        )
                        if message is None:
                            response = {
                                "success": False,
                                "message": "Message, session, username, or room is invalid.",
                            }
                        else:
                            response = {"success": True, "message": message}
                            self.broadcast(
                                {
                                    "type": "message",
                                    "payload": {
                                        "username": message["username"],
                                        "room": message["room"],
                                        "text": message["text"],
                                        "timestamp": message["timestamp"],
                                    },
                                },
                                exclude=conn,
                            )
                    elif event_type == "message":
                        response = {"success": False, "message": "Invalid message payload."}
                    else:
                        response = {"success": True}

                    conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except (ConnectionError, OSError, ValueError):
            pass
        finally:
            with self.lock:
                try:
                    self.sockets.remove(conn)
                except ValueError:
                    pass
            conn.close()

    def broadcast(self, payload, exclude=None):
        """Send one JSON event to connected TCP clients."""
        raw = (json.dumps(payload) + "\n").encode("utf-8")
        with self.lock:
            sockets = [conn for conn in self.sockets if conn is not exclude]
        for conn in sockets:
            try:
                conn.sendall(raw)
            except (ConnectionError, OSError):
                with self.lock:
                    try:
                        self.sockets.remove(conn)
                    except ValueError:
                        pass
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()

    def send_tcp_message(self, event_type: str, payload):
        """Send an event through TCP and wait for the server's response."""
        with socket.create_connection(
            (TCP_HOST, TCP_PORT),
            timeout=TCP_TIMEOUT_SECONDS,
        ) as client:
            client.sendall(
                (
                    json.dumps({"type": event_type, "payload": payload}) + "\n"
                ).encode("utf-8")
            )
            with client.makefile("rb") as reader:
                response = reader.readline()
            if not response:
                raise ConnectionError("TCP server closed without acknowledging the event.")
            result = json.loads(response.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError("TCP server returned an invalid response.")
            return result

    def stop(self):
        """Stop the TCP listener so the application can shut down cleanly."""
        self.running = False
        try:
            with socket.create_connection(
                (TCP_HOST, TCP_PORT),
                timeout=TCP_SHUTDOWN_TIMEOUT_SECONDS,
            ):
                pass
        except OSError:
            pass
        self.server.close()


TCP_SERVER = TCPChatServer()
TCP_SERVER.start()


def write_event(writer, event_type: str, payload):
    """Write one SSE event; the blank line marks the end of that event."""
    event = {"type": event_type, "payload": payload}
    encoded_event = f"data: {json.dumps(event)}\n\n".encode("utf-8")
    writer.write(encoded_event)
    writer.flush()


class ChatHTTPHandler(BaseHTTPRequestHandler):
    """Serve the chat UI, JSON API, and browser event streams."""

    server_version = "TCPChatServer/1.0"

    def do_OPTIONS(self):
        """Allow JSON API requests from other browser origins."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        """Keep the default HTTP request log quiet."""
        return

    def serve_static(self, relative_path: str):
        """Serve a UI file from static/, rejecting paths outside that folder."""
        if relative_path in ("", "/"):
            relative_path = "index.html"
        safe_path = (STATIC_DIR / relative_path.lstrip("/")).resolve()
        if STATIC_DIR.resolve() not in safe_path.parents and safe_path != STATIC_DIR.resolve():
            self.send_error(403, "Forbidden")
            return
        if not safe_path.exists() or not safe_path.is_file():
            self.send_error(404, "Not Found")
            return
        content = safe_path.read_bytes()
        self.send_response(200)
        content_type = (
            mimetypes.guess_type(str(safe_path))[0]
            or "application/octet-stream"
        )
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        """Handle browser event streams, read-only API calls, and UI files."""
        parsed = urlparse(self.path)
        if parsed.path == "/events":
            session_id = parse_qs(parsed.query).get("session", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            subscribed = False
            try:
                if session_id:
                    subscribed = STATE.subscribe(session_id, self.wfile)
                    if subscribed:
                        initial_state = STATE.snapshot_for(session_id)
                        write_event(self.wfile, "state", initial_state)
                while True:
                    # A heartbeat keeps an idle event stream open.
                    time.sleep(SSE_HEARTBEAT_SECONDS)
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                pass
            finally:
                if subscribed:
                    STATE.unsubscribe(session_id, self.wfile)
            return

        if parsed.path == "/api/status":
            payload = {"status": "ok", "rooms": list(ROOMS.keys()), "tcpPort": TCP_PORT}
            self._send_json(payload)
            return

        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self._send_json(STATE.search(query))
            return

        if parsed.path == "/api/state":
            session_id = parse_qs(parsed.query).get("session", [""])[0]
            state = STATE.snapshot_for(session_id) if session_id else {}
            self._send_json({"sessionId": session_id, "state": state})
            return

        if parsed.path.startswith("/api/"):
            self.send_error(404, "API endpoint not found")
            return

        self.serve_static(parsed.path)

    def do_POST(self):
        """Handle login, message, room-switch, and logout requests."""
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            payload = {}

        if parsed.path == "/api/login":
            if not isinstance(payload, dict):
                self._send_json({"success": False, "message": "Invalid request body."}, status=400)
                return
            username = (payload.get("username") or "").strip()
            display_name = (payload.get("displayName") or "").strip()
            if not username:
                self._send_json({"success": False, "message": "Username is required."}, status=400)
                return
            try:
                session_id = STATE.login(username, display_name)
                session = STATE.sessions[session_id]
                TCP_SERVER.send_tcp_message(
                    "join",
                    {
                        "username": session["username"],
                        "room": session["room"],
                    },
                )
                self._send_json(
                    {
                        "success": True,
                        "sessionId": session_id,
                        "state": STATE.snapshot_for(session_id),
                    }
                )
            except ValueError as exc:
                self._send_json({"success": False, "message": str(exc)}, status=400)
            return

        if parsed.path == "/api/send":
            if not isinstance(payload, dict):
                self._send_json({"success": False, "message": "Invalid request body."}, status=400)
                return
            session_id = payload.get("sessionId")
            message = payload.get("message")
            username = payload.get("username")
            room_name = payload.get("room")
            if not session_id or not username or not room_name:
                self._send_json(
                    {"success": False, "message": "Session, username, and room are required."},
                    status=400,
                )
                return

            try:
                result = TCP_SERVER.send_tcp_message(
                    "message",
                    {
                        "sessionId": session_id,
                        "username": username,
                        "room": room_name,
                        "text": message,
                    },
                )
            except (ConnectionError, OSError, ValueError) as exc:
                self._send_json(
                    {"success": False, "message": f"TCP relay failed: {exc}"},
                    status=503,
                )
                return
            if not result.get("success"):
                self._send_json(
                    {"success": False, "message": result.get("message", "Unable to send message.")},
                    status=400,
                )
                return
            self._send_json({"success": True, "message": result["message"]})
            return

        if parsed.path == "/api/switch-room":
            if not isinstance(payload, dict):
                self._send_json({"success": False, "message": "Invalid request body."}, status=400)
                return
            session_id = payload.get("sessionId")
            room_name = payload.get("room")
            if not session_id or not room_name:
                self._send_json(
                    {"success": False, "message": "Session and room are required."},
                    status=400,
                )
                return
            STATE.switch_room(session_id, room_name)
            TCP_SERVER.send_tcp_message("room_change", {"room": room_name, "sessionId": session_id})
            self._send_json({"success": True, "state": STATE.snapshot_for(session_id)})
            return

        if parsed.path == "/api/logout":
            if not isinstance(payload, dict):
                self._send_json({"success": False, "message": "Invalid request body."}, status=400)
                return
            session_id = payload.get("sessionId")
            if session_id:
                STATE.logout(session_id)
                TCP_SERVER.send_tcp_message("leave", {"sessionId": session_id})
            self._send_json({"success": True})
            return

        self.send_error(404, "API endpoint not found")

    def _send_json(self, data, status=200):
        """Send a JSON response with the expected content headers."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main():
    """Start both servers and open the chat page in the default browser."""
    STATE.add_system_message(DEFAULT_ROOM, "Server started successfully")
    http_server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), ChatHTTPHandler)
    local_chat_url = f"http://127.0.0.1:{HTTP_PORT}"
    print(f"HTTP server listening on {HTTP_HOST}:{HTTP_PORT}")
    print(f"Open locally: {local_chat_url}")
    print(f"TCP relay running at {TCP_HOST}:{TCP_PORT}")
    browser = threading.Timer(0.5, webbrowser.open, args=(local_chat_url,))
    browser.daemon = True
    browser.start()
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http_server.server_close()
        TCP_SERVER.stop()


if __name__ == "__main__":
    main()
