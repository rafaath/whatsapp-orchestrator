package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

type LiveEvent struct {
	Seq        int64  `json:"seq"`
	MessageID  string `json:"message_id"`
	Chat       string `json:"chat_jid"`
	Sender     string `json:"sender_jid"`
	FromOwner  bool   `json:"from_owner"`
	SelfChat   bool   `json:"self_chat"`
	Group      bool   `json:"group"`
	SentAt     int64  `json:"sent_at"`
	ReceivedAt int64  `json:"received_at"`
	Text       string `json:"text"`
}

func (store *MessageStore) initControl() error {
	_, err := store.db.Exec(`
		CREATE TABLE IF NOT EXISTS control_events (
		 seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL, chat_jid TEXT NOT NULL,
		 sender_jid TEXT NOT NULL, from_owner BOOLEAN NOT NULL, self_chat BOOLEAN NOT NULL,
		 is_group BOOLEAN NOT NULL, sent_at INTEGER NOT NULL, received_at INTEGER NOT NULL,
		 content TEXT NOT NULL, UNIQUE(message_id, chat_jid));
		CREATE TABLE IF NOT EXISTS control_sends (
		 request_id TEXT PRIMARY KEY, message_id TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL,
		 status TEXT NOT NULL, created_at INTEGER NOT NULL);
		UPDATE control_sends SET status='uncertain' WHERE status='sending';
		DELETE FROM control_events WHERE received_at < unixepoch()-2592000;
	`)
	return err
}

func isOwner(client *whatsmeow.Client, jid types.JID) bool {
	jid = jid.ToNonAD()
	return client.Store.ID != nil && (jid == client.Store.ID.ToNonAD() || (!client.Store.LID.IsEmpty() && jid == client.Store.LID.ToNonAD()))
}

func eligibleLiveMessage(msg *events.Message) bool {
	return msg != nil && msg.Message != nil && !msg.IsEdit && msg.SourceWebMsg == nil &&
		msg.UnavailableRequestID == "" && !msg.IsViewOnce && !msg.IsEphemeral &&
		msg.Message.GetProtocolMessage() == nil && msg.Info.ID != ""
}

func (store *MessageStore) appendLiveEvent(client *whatsmeow.Client, msg *events.Message) error {
	if !eligibleLiveMessage(msg) {
		return nil
	}
	content := extractTextContent(msg.Message)
	if content == "" || len(content) > 16000 {
		return nil
	}
	chat := msg.Info.Chat.ToNonAD()
	if chat.Server != types.DefaultUserServer && chat.Server != types.HiddenUserServer && chat.Server != types.GroupServer {
		return nil
	}
	// Reserve automation message IDs before sending, so echoes cannot become commands.
	result, err := store.db.Exec(`INSERT OR IGNORE INTO control_events
		(message_id,chat_jid,sender_jid,from_owner,self_chat,is_group,sent_at,received_at,content)
		SELECT ?,?,?,?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM control_sends WHERE message_id=?)`,
		msg.Info.ID, chat.String(), msg.Info.Sender.ToNonAD().String(), msg.Info.IsFromMe && isOwner(client, msg.Info.Sender),
		isOwner(client, chat), msg.Info.IsGroup, msg.Info.Timestamp.Unix(), time.Now().Unix(), content, msg.Info.ID)
	if err != nil {
		return err
	}
	if count, _ := result.RowsAffected(); count > 0 {
		store.wakeMu.Lock()
		close(store.wake)
		store.wake = make(chan struct{})
		store.wakeMu.Unlock()
	}
	return nil
}

func (store *MessageStore) liveEvents(after int64) ([]LiveEvent, error) {
	rows, err := store.db.Query(`SELECT seq,message_id,chat_jid,sender_jid,from_owner,self_chat,is_group,sent_at,received_at,content FROM control_events WHERE seq>? ORDER BY seq LIMIT 100`, after)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []LiveEvent{}
	for rows.Next() {
		var e LiveEvent
		if err := rows.Scan(&e.Seq, &e.MessageID, &e.Chat, &e.Sender, &e.FromOwner, &e.SelfChat, &e.Group, &e.SentAt, &e.ReceivedAt, &e.Text); err != nil {
			return nil, err
		}
		items = append(items, e)
	}
	return items, rows.Err()
}

func controlJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(value)
}

func registerControlAPI(mux *http.ServeMux, client *whatsmeow.Client, store *MessageStore) {
	mux.HandleFunc("GET /api/control/status", func(w http.ResponseWriter, r *http.Request) {
		var tail int64
		if err := store.db.QueryRow("SELECT COALESCE(MAX(seq),0) FROM control_events").Scan(&tail); err != nil {
			http.Error(w, "Event store unavailable", 500)
			return
		}
		owner := ""
		if client.Store.ID != nil {
			owner = client.Store.ID.ToNonAD().String()
		}
		controlJSON(w, map[string]any{"version": 1, "connected": client.IsConnected(), "authenticated": client.IsLoggedIn(), "owner_jid": owner, "owner_lid": client.Store.LID.ToNonAD().String(), "tail": tail})
	})
	mux.HandleFunc("GET /api/control/events", func(w http.ResponseWriter, r *http.Request) {
		after, err := strconv.ParseInt(r.URL.Query().Get("after"), 10, 64)
		if err != nil || after < 0 {
			http.Error(w, "Invalid event cursor", 400)
			return
		}
		store.wakeMu.Lock()
		wake := store.wake
		store.wakeMu.Unlock()
		items, err := store.liveEvents(after)
		if err != nil {
			http.Error(w, "Event store unavailable", 500)
			return
		}
		if len(items) == 0 && r.URL.Query().Get("wait") == "1" {
			select {
			case <-wake:
			case <-r.Context().Done():
				return
			case <-time.After(20 * time.Second):
			}
			items, err = store.liveEvents(after)
			if err != nil {
				http.Error(w, "Event store unavailable", 500)
				return
			}
		}
		controlJSON(w, map[string]any{"events": items})
	})
	mux.HandleFunc("GET /api/control/group", func(w http.ResponseWriter, r *http.Request) {
		jid, err := parseRecipient(r.URL.Query().Get("jid"))
		if err != nil || jid.Server != types.GroupServer {
			http.Error(w, "Exact group JID required", 400)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		group, err := client.GetGroupInfo(ctx, jid)
		if err != nil {
			http.Error(w, "Could not verify current group membership", 503)
			return
		}
		onlyOwner := len(group.Participants) == 1
		for _, p := range group.Participants {
			onlyOwner = onlyOwner && (isOwner(client, p.JID) || isOwner(client, p.PhoneNumber) || isOwner(client, p.LID))
		}
		controlJSON(w, map[string]any{"jid": jid.String(), "name": group.Name, "owner_only": onlyOwner, "participant_count": len(group.Participants)})
	})
	mux.HandleFunc("POST /api/control/send", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			RequestID string `json:"request_id"`
			Recipient string `json:"recipient"`
			Message   string `json:"message"`
		}
		if err := decodeRequest(w, r, &req); err != nil {
			http.Error(w, "Invalid request", 400)
			return
		}
		jid, err := parseRecipient(req.Recipient)
		if err != nil || !regexp.MustCompile(`^[A-Za-z0-9:_-]{8,160}$`).MatchString(req.RequestID) || strings.TrimSpace(req.Message) == "" || len(req.Message) > 16000 {
			http.Error(w, "Invalid recipient, message or request ID", 400)
			return
		}
		hash := sha256.Sum256([]byte(jid.String() + "\x00" + req.Message))
		digest := hex.EncodeToString(hash[:])
		var status, id, oldHash string
		err = store.db.QueryRow("SELECT status,message_id,payload_hash FROM control_sends WHERE request_id=?", req.RequestID).Scan(&status, &id, &oldHash)
		if err == nil {
			if digest != oldHash {
				http.Error(w, "Idempotency key reused with a different message", 409)
				return
			}
			if status == "sending" {
				status = "uncertain"
			}
			controlJSON(w, map[string]any{"status": status, "message_id": id})
			return
		}
		if err != sql.ErrNoRows {
			http.Error(w, "Send ledger unavailable", 500)
			return
		}
		if !client.IsConnected() || !client.IsLoggedIn() {
			http.Error(w, "Bridge offline; no send attempted", 503)
			return
		}
		id = client.GenerateMessageID()
		if _, err = store.db.Exec("INSERT INTO control_sends VALUES(?,?,?,'sending',?)", req.RequestID, id, digest, time.Now().Unix()); err != nil {
			http.Error(w, "Could not reserve send; query with the same request ID", 409)
			return
		}
		// A disconnected caller must not cancel the acknowledgement bookkeeping.
		ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
		defer cancel()
		response, sendErr := client.SendMessage(ctx, jid, &waProto.Message{Conversation: proto.String(req.Message)}, whatsmeow.SendRequestExtra{ID: id})
		status = "acknowledged"
		if sendErr != nil {
			status = "uncertain"
		}
		if _, err = store.db.Exec("UPDATE control_sends SET status=? WHERE request_id=?", status, req.RequestID); err != nil {
			status = "uncertain"
		}
		if sendErr == nil {
			store.StoreChat(jid.String(), "", response.Timestamp)
			store.StoreMessage(id, jid.String(), client.Store.ID.User, req.Message, response.Timestamp, true, "", "", "", nil, nil, nil, 0)
		}
		controlJSON(w, map[string]any{"status": status, "message_id": id})
	})
	mux.HandleFunc("GET /api/control/send-status", func(w http.ResponseWriter, r *http.Request) {
		var status, id string
		if err := store.db.QueryRow("SELECT status,message_id FROM control_sends WHERE request_id=?", r.URL.Query().Get("id")).Scan(&status, &id); err != nil {
			http.Error(w, "Attempt not found", 404)
			return
		}
		controlJSON(w, map[string]string{"status": status, "message_id": id})
	})
}
