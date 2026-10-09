package main

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.mau.fi/whatsmeow"
	waProto "go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	"google.golang.org/protobuf/proto"
)

func TestControlEventsDoNotReplayHistoryEditsOrOwnReplies(t *testing.T) {
	t.Setenv("WHATSAPP_STORE_DIR", t.TempDir())
	store, err := NewMessageStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	container, err := sqlstore.New(context.Background(), "sqlite", sqliteDSN("devices.db"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer container.Close()
	device, err := container.GetFirstDevice(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	owner := types.NewJID("15550000001", types.DefaultUserServer)
	device.ID = &owner
	client := whatsmeow.NewClient(device, nil)
	msg := &events.Message{Info: types.MessageInfo{MessageSource: types.MessageSource{Chat: owner, Sender: owner, IsFromMe: true}, ID: "TEST1", Timestamp: time.Now()}, Message: &waProto.Message{Conversation: proto.String("pineapple")}}
	if err = store.appendLiveEvent(client, msg); err != nil {
		t.Fatal(err)
	}
	store.appendLiveEvent(client, msg)
	msg.Info.ID = "EDIT"
	msg.IsEdit = true
	store.appendLiveEvent(client, msg)
	msg.IsEdit = false
	msg.Info.ID = "REFETCH"
	msg.UnavailableRequestID = "old"
	store.appendLiveEvent(client, msg)
	msg.UnavailableRequestID = ""
	msg.Info.ID = "BOT"
	store.db.Exec("INSERT INTO control_sends VALUES('request-1','BOT','hash','sending',0)")
	store.appendLiveEvent(client, msg)
	items, err := store.liveEvents(0)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 1 || !items[0].FromOwner || !items[0].SelfChat {
		t.Fatalf("unexpected live events: %+v", items)
	}
	msg.Info.ID = "OTHER"
	msg.Info.IsFromMe = false
	store.appendLiveEvent(client, msg)
	items, _ = store.liveEvents(items[0].Seq)
	if len(items) != 1 || items[0].FromOwner {
		t.Fatal("another sender gained owner privileges")
	}
}

func TestControlAPIRequiresAuthorizationAndPreservesSendStatus(t *testing.T) {
	t.Setenv("WHATSAPP_STORE_DIR", t.TempDir())
	store, _ := NewMessageStore()
	defer store.Close()
	container, _ := sqlstore.New(context.Background(), "sqlite", sqliteDSN("devices.db"), nil)
	defer container.Close()
	device, _ := container.GetFirstDevice(context.Background())
	client := whatsmeow.NewClient(device, nil)
	handler := apiHandler(client, store, "token")
	request := httptest.NewRequest("GET", "/api/control/events?after=0", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, request)
	if w.Code != 401 {
		t.Fatal("event journal exposed without auth")
	}
	request.Header.Set("Authorization", "Bearer token")
	w = httptest.NewRecorder()
	handler.ServeHTTP(w, request)
	var result struct {
		Events []LiveEvent `json:"events"`
	}
	json.Unmarshal(w.Body.Bytes(), &result)
	if w.Code != 200 || result.Events == nil {
		t.Fatal("bad empty journal response")
	}
	request = httptest.NewRequest("POST", "/api/control/send", strings.NewReader(`{"request_id":"request-2","recipient":"15550000002","message":"fixture"}`))
	request.Header.Set("Authorization", "Bearer token")
	request.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	handler.ServeHTTP(w, request)
	if w.Code != 503 {
		t.Fatal("offline send must fail before reserving an attempt")
	}
	var count int
	store.db.QueryRow("SELECT count(*) FROM control_sends").Scan(&count)
	if count != 0 {
		t.Fatal("offline request reserved a send")
	}
}
