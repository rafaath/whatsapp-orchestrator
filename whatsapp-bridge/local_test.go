package main

import (
	"context"
	"encoding/binary"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store/sqlstore"
)

func TestLocalBridgeInvariants(t *testing.T) {
	t.Setenv("WHATSAPP_STORE_DIR", t.TempDir())
	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	now := time.Date(2026, 10, 4, 12, 0, 0, 0, time.UTC)
	jid := "15551234567@s.whatsapp.net"
	if err := store.StoreChat(jid, "Example", now); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreMessage("message-1", jid, "15551234567", "Fixture", now, false, "", "", "", nil, nil, nil, 0); err != nil {
		t.Fatal(err)
	}
	if err := store.StoreChat(jid, "Example", now.Add(-time.Hour)); err != nil {
		t.Fatal(err)
	}
	var timestamp string
	if err := store.db.QueryRow("SELECT CAST(last_message_time AS TEXT) FROM chats WHERE jid=?", jid).Scan(&timestamp); err != nil {
		t.Fatal(err)
	}
	if timestamp != databaseTimestamp(now) {
		t.Fatalf("history moved the chat backward: %s", timestamp)
	}
	var count int
	store.db.QueryRow("SELECT count(*) FROM messages").Scan(&count)
	if count != 1 {
		t.Fatal("updating the chat destroyed its messages")
	}
	if err := store.StoreMessage("orphan", "missing", "sender", "text", now, false, "", "", "", nil, nil, nil, 0); err == nil {
		t.Fatal("foreign keys are not enforced")
	}

	token, err := loadAPIToken()
	if err != nil {
		t.Fatal(err)
	}
	stored, err := loadAPIToken()
	if err != nil || stored != token || len(token) != 64 {
		t.Fatal("token was not persisted correctly")
	}
	container, err := sqlstore.New(context.Background(), "sqlite", sqliteDSN("test-device.db"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer container.Close()
	device, err := container.GetFirstDevice(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	client := whatsmeow.NewClient(device, nil)
	handler := apiHandler(client, store, token)
	for _, test := range []struct {
		name, method, path, body, authorization, origin string
		status                                          int
	}{
		{"unauthenticated", "GET", "/api/health", "", "", "", 401},
		{"wrong token", "GET", "/api/health", "", "Bearer wrong", "", 401},
		{"missing scheme", "GET", "/api/health", "", token, "", 401},
		{"browser blocked", "GET", "/api/health", "", "Bearer " + token, "https://example.org", 403},
		{"offline health", "GET", "/api/health", "", "Bearer " + token, "", 200},
		{"send blocked while offline", "POST", "/api/send", `{"recipient":"15551234567","message":"fixture"}`, "Bearer " + token, "", 503},
		{"invalid recipient", "POST", "/api/send", `{"recipient":"../bad","message":"fixture"}`, "Bearer " + token, "", 400},
		{"unknown input", "POST", "/api/send", `{"recipient":"15551234567","message":"fixture","extra":1}`, "Bearer " + token, "", 400},
		{"second JSON object", "POST", "/api/send", `{"recipient":"15551234567","message":"fixture"}{}`, "Bearer " + token, "", 400},
		{"body limit", "POST", "/api/send", `{"message":"` + strings.Repeat("x", 1<<20) + `"}`, "Bearer " + token, "", 400},
	} {
		t.Run(test.name, func(t *testing.T) {
			r := httptest.NewRequest(test.method, test.path, strings.NewReader(test.body))
			r.Header.Set("Authorization", test.authorization)
			r.Header.Set("Origin", test.origin)
			r.Header.Set("Content-Type", "application/json")
			w := httptest.NewRecorder()
			handler.ServeHTTP(w, r)
			if w.Code != test.status {
				t.Fatalf("got %d, expected %d: %s", w.Code, test.status, w.Body.String())
			}
		})
	}
}

func TestPathsAndRecipients(t *testing.T) {
	t.Setenv("WHATSAPP_STORE_DIR", t.TempDir())
	for _, value := range []string{"15551234567", "15551234567@s.whatsapp.net", "123456789@lid", "123456789-123456@g.us"} {
		if _, err := parseRecipient(value); err != nil {
			t.Fatalf("valid recipient %q: %v", value, err)
		}
	}
	for _, value := range []string{"", "+15551234567", "12", "abc@s.whatsapp.net", "123@broadcast", "123/../456@lid", "15551234567:1@s.whatsapp.net"} {
		if _, err := parseRecipient(value); err == nil {
			t.Fatalf("invalid recipient accepted: %q", value)
		}
	}
	for _, filename := range []string{"../../../api-token", `..\..\secret`, "/etc/passwd", ".."} {
		path := mediaDestination("../../chat", "../message", filename)
		relative, err := filepath.Rel(filepath.Join(runtimeDir(), "media"), path)
		if err != nil || strings.HasPrefix(relative, "..") {
			t.Fatalf("attachment escaped runtime: %s", path)
		}
	}
}

func TestTruncatedVoiceNoteDoesNotPanic(t *testing.T) {
	data := make([]byte, 29)
	copy(data, "OggS")
	data[26] = 1
	data[27] = 255
	binary.LittleEndian.PutUint64(data[6:14], 48000)
	if _, _, err := analyzeOggOpus(data); err == nil {
		t.Fatal("truncated audio accepted")
	}
}
