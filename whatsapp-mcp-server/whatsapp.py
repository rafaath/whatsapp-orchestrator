import sqlite3
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, List, Tuple
import os
import logging
import re
from pathlib import Path
import requests
import json
import audio

STORE_DIR = Path(os.environ.get("WHATSAPP_STORE_DIR", Path(__file__).resolve().parent.parent / "runtime"))
MESSAGES_DB_PATH = str(STORE_DIR / "messages.db")
WHATSAPP_API_BASE_URL = "http://127.0.0.1:8765/api"
logger = logging.getLogger(__name__)


def open_database():
    if not Path(MESSAGES_DB_PATH).is_file():
        raise RuntimeError("Message store is not ready. Start Start-WhatsApp.ps1 and link your phone first.")
    return sqlite3.connect(Path(MESSAGES_DB_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def validate_page(limit: int, page: int):
    if not 1 <= limit <= 100 or page < 0:
        raise ValueError("limit must be 1..100 and page must be nonnegative")


def validate_context(before: int, after: int):
    if not 0 <= before <= 20 or not 0 <= after <= 20:
        raise ValueError("before and after must be 0..20")


def database_timestamp(value: str) -> str:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def api_request(endpoint: str, payload=None):
    try:
        token = (STORE_DIR / "api-token").read_text().strip()
    except OSError as exc:
        raise RuntimeError("Bridge is not ready. Start Start-WhatsApp.ps1 first.") from exc
    # Never inherit a proxy or netrc credentials for this local private service.
    with requests.Session() as session:
        session.trust_env = False
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if payload is None:
                response = session.get(f"{WHATSAPP_API_BASE_URL}/{endpoint}", headers=headers, timeout=(3, 5))
            else:
                response = session.post(f"{WHATSAPP_API_BASE_URL}/{endpoint}", json=payload, headers=headers, timeout=(3, 170))
        except requests.ConnectTimeout as exc:
            raise RuntimeError("Could not connect to the local bridge. No automatic retry was made.") from exc
        except requests.ReadTimeout as exc:
            raise RuntimeError("Bridge response timed out. The action may already have completed; do not automatically retry a send.") from exc
        except requests.ConnectionError as exc:
            raise RuntimeError("Bridge connection failed or closed. Start the bridge; do not automatically repeat an uncertain send.") from exc
        if response.status_code != 200:
            raise RuntimeError(f"Bridge HTTP {response.status_code}: {response.text[:600]}")
        return response.json()


def get_connection_status():
    try:
        return api_request("health")
    except (RuntimeError, requests.RequestException, ValueError) as exc:
        return {"connected": False, "authenticated": False, "message": str(exc)}

@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None

@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        """Determine if chat is a group based on JID pattern."""
        return self.jid.endswith("@g.us")

@dataclass
class Contact:
    phone_number: Optional[str]
    name: Optional[str]
    jid: str

@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]

def get_sender_name(sender_jid: str) -> str:
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT name FROM contacts WHERE jid=? OR phone_number=?
            UNION ALL
            SELECT name FROM chats WHERE jid IN (?, ?, ?)
            LIMIT 1
        """, (sender_jid, sender_jid if '@' not in sender_jid else None,
              sender_jid, f"{sender_jid}@s.whatsapp.net", f"{sender_jid}@lid"))
        
        result = cursor.fetchone()
        
        
        if result and result[0]:
            return result[0]
        else:
            return sender_jid
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        return sender_jid
    finally:
        if 'conn' in locals():
            conn.close()

def format_message(message: Message, show_chat_info: bool = True) -> None:
    """Print a single message with consistent formatting."""
    output = ""
    
    if show_chat_info and message.chat_name:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] Chat: {message.chat_name} "
    else:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] "
        
    content_prefix = ""
    if hasattr(message, 'media_type') and message.media_type:
        content_prefix = f"[{message.media_type} - Message ID: {message.id} - Chat JID: {message.chat_jid}] "
    
    try:
        sender_name = get_sender_name(message.sender) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{message.content}\n"
    except Exception as e:
        logger.warning("Could not format sender metadata")
    return output

def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> None:
    output = ""
    if not messages:
        output += "No messages to display."
        return output
    
    for message in messages:
        output += format_message(message, show_chat_info)
    return output

def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1
) -> str:
    """Get messages matching the specified criteria with optional context."""
    validate_page(limit, page)
    validate_context(context_before, context_after)
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        # Build base query
        query_parts = ["SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type FROM messages"]
        query_parts.append("JOIN chats ON messages.chat_jid = chats.jid")
        where_clauses = []
        params = []
        
        # Add filters
        if after:
            try:
                after = database_timestamp(after)
            except ValueError:
                raise ValueError(f"Invalid date format for 'after': {after}. Please use ISO-8601 format.")
            
            where_clauses.append("messages.timestamp > ?")
            params.append(after)

        if before:
            try:
                before = database_timestamp(before)
            except ValueError:
                raise ValueError(f"Invalid date format for 'before': {before}. Please use ISO-8601 format.")
            
            where_clauses.append("messages.timestamp < ?")
            params.append(before)

        if sender_phone_number:
            where_clauses.append("messages.sender = ?")
            params.append(sender_phone_number)
            
        if chat_jid:
            where_clauses.append("messages.chat_jid = ?")
            params.append(chat_jid)
            
        if query:
            where_clauses.append("LOWER(messages.content) LIKE LOWER(?)")
            params.append(f"%{query}%")
            
        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))
            
        # Add pagination
        offset = page * limit
        query_parts.append("ORDER BY messages.timestamp DESC, messages.rowid DESC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        
        cursor.execute(" ".join(query_parts), tuple(params))
        messages = cursor.fetchall()
        
        result = []
        for msg in messages:
            message = Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=msg[2],
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7]
            )
            result.append(message)
            
        if include_context and result:
            # Add context for each message
            messages_with_context = []
            for msg in result:
                context = get_message_context(msg.id, context_before, context_after, msg.chat_jid)
                messages_with_context.extend(context.before)
                messages_with_context.append(context.message)
                messages_with_context.extend(context.after)
            
            return format_messages_list(messages_with_context, show_chat_info=True)
            
        # Format and display messages without context
        return format_messages_list(result, show_chat_info=True)    
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5,
    chat_jid: Optional[str] = None
) -> MessageContext:
    """Get context around a specific message."""
    validate_context(before, after)
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        # Get the target message first
        cursor.execute("""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.chat_jid, messages.media_type, messages.rowid
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.id = ? AND (? IS NULL OR messages.chat_jid = ?)
            LIMIT 2
        """, (message_id, chat_jid, chat_jid))
        matches = cursor.fetchall()
        if len(matches) > 1:
            raise ValueError("This message ID occurs in multiple chats. Supply chat_jid.")
        msg_data = matches[0] if matches else None
        
        if not msg_data:
            raise ValueError(f"Message with ID {message_id} not found")
            
        target_message = Message(
            timestamp=datetime.fromisoformat(msg_data[0]),
            sender=msg_data[1],
            chat_name=msg_data[2],
            content=msg_data[3],
            is_from_me=msg_data[4],
            chat_jid=msg_data[5],
            id=msg_data[6],
            media_type=msg_data[8]
        )
        
        # Get messages before
        cursor.execute("""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.chat_jid = ? AND (messages.timestamp < ? OR (messages.timestamp = ? AND messages.rowid < ?))
            ORDER BY messages.timestamp DESC, messages.rowid DESC
            LIMIT ?
        """, (msg_data[7], msg_data[0], msg_data[0], msg_data[9], before))
        
        before_messages = []
        for msg in cursor.fetchall():
            before_messages.append(Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=msg[2],
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7]
            ))
        
        # Get messages after
        cursor.execute("""
            SELECT messages.timestamp, messages.sender, chats.name, messages.content, messages.is_from_me, chats.jid, messages.id, messages.media_type
            FROM messages
            JOIN chats ON messages.chat_jid = chats.jid
            WHERE messages.chat_jid = ? AND (messages.timestamp > ? OR (messages.timestamp = ? AND messages.rowid > ?))
            ORDER BY messages.timestamp ASC, messages.rowid ASC
            LIMIT ?
        """, (msg_data[7], msg_data[0], msg_data[0], msg_data[9], after))
        
        after_messages = []
        for msg in cursor.fetchall():
            after_messages.append(Message(
                timestamp=datetime.fromisoformat(msg[0]),
                sender=msg[1],
                chat_name=msg[2],
                content=msg[3],
                is_from_me=msg[4],
                chat_jid=msg[5],
                id=msg[6],
                media_type=msg[7]
            ))
        
        return MessageContext(
            message=target_message,
            before=before_messages,
            after=after_messages
        )
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        raise
    finally:
        if 'conn' in locals():
            conn.close()


def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Chat]:
    """Get chats matching the specified criteria."""
    validate_page(limit, page)
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        # Build base query
        query_parts = ["""
            SELECT 
                chats.jid,
                chats.name,
                chats.last_message_time,
                messages.content as last_message,
                messages.sender as last_sender,
                messages.is_from_me as last_is_from_me
            FROM chats
        """]
        
        if not include_last_message:
            query_parts[0] = query_parts[0].replace("messages.content", "NULL").replace("messages.sender", "NULL").replace("messages.is_from_me", "NULL")
        else:
            query_parts.append("""
                LEFT JOIN messages ON messages.rowid = (
                    SELECT rowid FROM messages WHERE chat_jid = chats.jid
                    ORDER BY timestamp DESC, rowid DESC LIMIT 1
                )
            """)
            
        where_clauses = []
        params = []
        
        if query:
            where_clauses.append("(LOWER(chats.name) LIKE LOWER(?) OR chats.jid LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
            
        if where_clauses:
            query_parts.append("WHERE " + " AND ".join(where_clauses))
            
        # Add sorting
        order_by = "chats.last_message_time DESC" if sort_by == "last_active" else "chats.name"
        query_parts.append(f"ORDER BY {order_by}")
        
        # Add pagination
        offset = (page ) * limit
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])
        
        cursor.execute(" ".join(query_parts), tuple(params))
        chats = cursor.fetchall()
        
        result = []
        for chat_data in chats:
            chat = Chat(
                jid=chat_data[0],
                name=chat_data[1],
                last_message_time=datetime.fromisoformat(chat_data[2]) if chat_data[2] else None,
                last_message=chat_data[3],
                last_sender=chat_data[4],
                last_is_from_me=chat_data[5]
            )
            result.append(chat)
            
        return result
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def search_contacts(query: str) -> List[Contact]:
    """Search contacts by name or phone number."""
    if not query.strip():
        raise ValueError("Provide a contact name or phone number to search for")
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        # Split query into characters to support partial matching
        search_pattern = '%' +query + '%'
        
        cursor.execute("""
            WITH candidates AS (
                SELECT jid, phone_number, name FROM contacts
                UNION ALL
                SELECT jid,
                    CASE WHEN jid LIKE '%@s.whatsapp.net' THEN substr(jid, 1, instr(jid, '@')-1) END,
                    name FROM chats
                WHERE jid NOT LIKE '%@g.us' AND NOT EXISTS (SELECT 1 FROM contacts WHERE contacts.jid=chats.jid)
            ), ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY COALESCE(phone_number, jid)
                    ORDER BY EXISTS (SELECT 1 FROM chats WHERE chats.jid=candidates.jid) DESC, jid
                ) AS rank FROM candidates
            )
            SELECT jid, name, phone_number FROM ranked
            WHERE rank=1 AND (LOWER(name) LIKE LOWER(?) OR jid LIKE ? OR phone_number LIKE ?)
            ORDER BY name, jid LIMIT 50
        """, (search_pattern, search_pattern, search_pattern))
        
        contacts = cursor.fetchall()
        
        result = []
        for contact_data in contacts:
            contact = Contact(
                phone_number=contact_data[2],
                name=contact_data[1],
                jid=contact_data[0]
            )
            result.append(contact)
            
        return result
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Chat]:
    """Get all chats involving the contact.
    
    Args:
        jid: The contact's JID to search for
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
    """
    validate_page(limit, page)
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT
                c.jid,
                c.name,
                c.last_message_time,
                m.content as last_message,
                m.sender as last_sender,
                m.is_from_me as last_is_from_me
            FROM chats c
            LEFT JOIN messages m ON m.rowid = (
                SELECT rowid FROM messages WHERE chat_jid = c.jid ORDER BY timestamp DESC, rowid DESC LIMIT 1
            )
            WHERE c.jid = ? OR EXISTS (
                SELECT 1 FROM messages participant WHERE participant.chat_jid = c.jid AND participant.sender IN (?, ?)
            )
            ORDER BY c.last_message_time DESC
            LIMIT ? OFFSET ?
        """, (jid, jid, jid.split('@')[0], limit, page * limit))
        
        chats = cursor.fetchall()
        
        result = []
        for chat_data in chats:
            chat = Chat(
                jid=chat_data[0],
                name=chat_data[1],
                last_message_time=datetime.fromisoformat(chat_data[2]) if chat_data[2] else None,
                last_message=chat_data[3],
                last_sender=chat_data[4],
                last_is_from_me=chat_data[5]
            )
            result.append(chat)
            
        return result
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        return []
    finally:
        if 'conn' in locals():
            conn.close()


def get_last_interaction(jid: str) -> str:
    """Get most recent message involving the contact."""
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                m.timestamp,
                m.sender,
                c.name,
                m.content,
                m.is_from_me,
                c.jid,
                m.id,
                m.media_type
            FROM messages m
            JOIN chats c ON m.chat_jid = c.jid
            WHERE m.sender IN (?, ?) OR c.jid = ?
            ORDER BY m.timestamp DESC
            LIMIT 1
        """, (jid, jid.split('@')[0], jid))
        
        msg_data = cursor.fetchone()
        
        if not msg_data:
            return None
            
        message = Message(
            timestamp=datetime.fromisoformat(msg_data[0]),
            sender=msg_data[1],
            chat_name=msg_data[2],
            content=msg_data[3],
            is_from_me=msg_data[4],
            chat_jid=msg_data[5],
            id=msg_data[6],
            media_type=msg_data[7]
        )
        
        return format_message(message)
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_chat(chat_jid: str, include_last_message: bool = True) -> Optional[Chat]:
    """Get chat metadata by JID."""
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        query = """
            SELECT 
                c.jid,
                c.name,
                c.last_message_time,
                m.content as last_message,
                m.sender as last_sender,
                m.is_from_me as last_is_from_me
            FROM chats c
        """
        
        if not include_last_message:
            query = query.replace("m.content", "NULL").replace("m.sender", "NULL").replace("m.is_from_me", "NULL")
        else:
            query += """
                LEFT JOIN messages m ON m.rowid = (
                    SELECT rowid FROM messages WHERE chat_jid = c.jid ORDER BY timestamp DESC, rowid DESC LIMIT 1
                )
            """
            
        query += " WHERE c.jid = ?"
        
        cursor.execute(query, (chat_jid,))
        chat_data = cursor.fetchone()
        
        if not chat_data:
            return None
            
        return Chat(
            jid=chat_data[0],
            name=chat_data[1],
            last_message_time=datetime.fromisoformat(chat_data[2]) if chat_data[2] else None,
            last_message=chat_data[3],
            last_sender=chat_data[4],
            last_is_from_me=chat_data[5]
        )
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        return None
    finally:
        if 'conn' in locals():
            conn.close()


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Chat]:
    """Get chat metadata by sender phone number."""
    if not re.fullmatch(r"[1-9][0-9]{6,14}", sender_phone_number):
        raise ValueError("Use the full phone number with country code, without + or spaces")
    try:
        conn = open_database()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                c.jid,
                c.name,
                c.last_message_time,
                m.content as last_message,
                m.sender as last_sender,
                m.is_from_me as last_is_from_me
            FROM chats c
            LEFT JOIN messages m ON m.rowid = (
                SELECT rowid FROM messages WHERE chat_jid = c.jid ORDER BY timestamp DESC, rowid DESC LIMIT 1
            )
            WHERE c.jid = ? OR c.jid IN (SELECT jid FROM contacts WHERE phone_number=?)
            ORDER BY c.last_message_time DESC
            LIMIT 1
        """, (f"{sender_phone_number}@s.whatsapp.net", sender_phone_number))
        
        chat_data = cursor.fetchone()
        
        if not chat_data:
            return None
            
        return Chat(
            jid=chat_data[0],
            name=chat_data[1],
            last_message_time=datetime.fromisoformat(chat_data[2]) if chat_data[2] else None,
            last_message=chat_data[3],
            last_sender=chat_data[4],
            last_is_from_me=chat_data[5]
        )
        
    except sqlite3.Error as e:
        raise RuntimeError("Message database query failed; check bridge status") from e
        return None
    finally:
        if 'conn' in locals():
            conn.close()

def _send(payload) -> Tuple[bool, str]:
    try:
        result = api_request("send", payload)
        return result.get("success", False), result.get("message", "Unknown bridge response")
    except (RuntimeError, requests.RequestException, ValueError) as exc:
        return False, str(exc)


def send_message(recipient: str, message: str) -> Tuple[bool, str]:
    if not recipient or not message.strip():
        return False, "A recipient and nonempty message are required"
    return _send({"recipient": recipient, "message": message})


def send_file(recipient: str, media_path: str) -> Tuple[bool, str]:
    if not recipient or not media_path:
        return False, "A recipient and media path are required"
    path = Path(media_path).expanduser().resolve()
    if not path.is_file() or not 0 < path.stat().st_size <= 32 * 1024 * 1024:
        return False, "Media must be a nonempty regular file of at most 32 MiB"
    return _send({"recipient": recipient, "media_path": str(path)})


def send_audio_message(recipient: str, media_path: str) -> Tuple[bool, str]:
    if not recipient or not Path(media_path).is_file():
        return False, "A recipient and existing audio file are required"
    converted_path = None
    try:
        if not media_path.lower().endswith(".ogg"):
            converted_path = audio.convert_to_opus_ogg_temp(media_path)
        return send_file(recipient, converted_path or media_path)
    except (OSError, RuntimeError) as exc:
        return False, f"Audio conversion failed: {exc}. Use send_file for raw audio; voice-note conversion requires ffmpeg."
    finally:
        if converted_path:
            Path(converted_path).unlink(missing_ok=True)


def download_media(message_id: str, chat_jid: str) -> Optional[str]:
    """Download only the requested attachment; errors propagate through MCP, not stdout."""
    if not message_id or not chat_jid:
        raise ValueError("A message ID and exact chat JID are required")
    result = api_request("download", {"message_id": message_id, "chat_jid": chat_jid})
    if not result.get("success"):
        raise RuntimeError(result.get("message", "Media download failed"))
    path = Path(result["path"]).resolve()
    if not path.is_relative_to(STORE_DIR.resolve() / "media"):
        raise RuntimeError("Bridge returned an attachment path outside its media directory")
    return str(path)
