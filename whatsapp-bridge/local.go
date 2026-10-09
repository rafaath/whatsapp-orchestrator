package main

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"context"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
	"rsc.io/qr"
)

const maxMediaBytes = 32 << 20

var phonePattern = regexp.MustCompile(`^[1-9][0-9]{6,14}$`)
var jidUserPattern = regexp.MustCompile(`^[0-9]+(?:-[0-9]+)?$`)

func runtimeDir() string {
	if path := os.Getenv("WHATSAPP_STORE_DIR"); path != "" {
		return path
	}
	return "store"
}

func sqliteDSN(name string) string {
	path, _ := filepath.Abs(filepath.Join(runtimeDir(), name))
	uri := url.URL{Scheme: "file", Path: filepath.ToSlash(path)}
	return uri.String() + "?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)"
}

func databaseTimestamp(timestamp time.Time) string {
	return timestamp.UTC().Format("2006-01-02T15:04:05.000000000Z")
}

func writePairingQR(code string) error {
	encoded, err := qr.Encode(code, qr.M)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(runtimeDir(), "pairing-qr.png"), encoded.PNG(), 0600)
}

func loadAPIToken() (string, error) {
	path := filepath.Join(runtimeDir(), "api-token")
	if data, err := os.ReadFile(path); err == nil {
		token := strings.TrimSpace(string(data))
		if len(token) != 64 {
			return "", fmt.Errorf("invalid API token file; restore it before starting the bridge")
		}
		if _, err := hex.DecodeString(token); err != nil {
			return "", fmt.Errorf("invalid API token format")
		}
		return token, nil
	} else if !os.IsNotExist(err) {
		return "", err
	}
	var random [32]byte
	if _, err := rand.Read(random[:]); err != nil {
		return "", err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return "", err
	}
	token := hex.EncodeToString(random[:])
	_, writeErr := io.WriteString(file, token+"\n")
	closeErr := file.Close()
	if writeErr != nil {
		return "", writeErr
	}
	return token, closeErr
}

func localAuthorization(next http.Handler, token string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if r.Header.Get("Origin") != "" {
			http.Error(w, "Browser-origin requests are not allowed", http.StatusForbidden)
			return
		}
		provided := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") || subtle.ConstantTimeCompare([]byte(provided), []byte(token)) != 1 {
			http.Error(w, "Unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func decodeRequest(w http.ResponseWriter, r *http.Request, target any) error {
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/json") {
		return fmt.Errorf("Content-Type must be application/json")
	}
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return fmt.Errorf("expected one JSON object")
	}
	return nil
}

func parseRecipient(recipient string) (types.JID, error) {
	if strings.Contains(recipient, "@") {
		jid, err := types.ParseJID(recipient)
		if err != nil || jid.Device != 0 || jid.RawAgent != 0 || !jidUserPattern.MatchString(jid.User) {
			return types.JID{}, fmt.Errorf("invalid recipient JID")
		}
		switch jid.Server {
		case types.DefaultUserServer, types.HiddenUserServer, types.GroupServer:
			return jid, nil
		default:
			return types.JID{}, fmt.Errorf("unsupported recipient server")
		}
	}
	if !phonePattern.MatchString(recipient) {
		return types.JID{}, fmt.Errorf("use a complete phone number including country code, without spaces or +")
	}
	return types.NewJID(recipient, types.DefaultUserServer), nil
}

func validateMediaFile(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return fmt.Errorf("cannot read media file: %w", err)
	}
	if !info.Mode().IsRegular() || info.Size() == 0 || info.Size() > maxMediaBytes {
		return fmt.Errorf("media must be a nonempty regular file of at most 32 MiB")
	}
	return nil
}

func mediaDestination(chatJID, messageID, filename string) string {
	chatHash := sha256.Sum256([]byte(chatJID))
	messageHash := sha256.Sum256([]byte(messageID))
	// Remote filenames and JIDs must never determine paths outside the media folder.
	filename = filepath.Base(strings.ReplaceAll(filename, "\\", "/"))
	if filename == "." || filename == ".." || filename == "" {
		filename = "media"
	}
	return filepath.Join(runtimeDir(), "media", hex.EncodeToString(chatHash[:8]), hex.EncodeToString(messageHash[:8])+"_"+filename)
}

func contactName(info types.ContactInfo, fallback string) string {
	for _, name := range []string{info.FullName, info.FirstName, info.PushName, info.BusinessName} {
		if name != "" {
			return name
		}
	}
	return fallback
}

func (store *MessageStore) storeContact(client *whatsmeow.Client, jid types.JID, name string) error {
	jid = jid.ToNonAD()
	if jid.Server != types.DefaultUserServer && jid.Server != types.HiddenUserServer {
		return nil
	}
	var phone any
	if jid.Server == types.DefaultUserServer {
		phone = jid.User
	} else if alternate, err := client.Store.GetAltJID(context.Background(), jid); err == nil && alternate.Server == types.DefaultUserServer {
		phone = alternate.User
	}
	_, err := store.db.Exec(`INSERT INTO contacts(jid, phone_number, name) VALUES (?, ?, ?)
		ON CONFLICT(jid) DO UPDATE SET phone_number=COALESCE(excluded.phone_number, contacts.phone_number),
		name=CASE WHEN excluded.name != '' THEN excluded.name ELSE contacts.name END`, jid.String(), phone, name)
	if err != nil {
		return err
	}
	_, err = store.db.Exec("UPDATE chats SET name=? WHERE jid=?", name, jid.String())
	return err
}

func (store *MessageStore) syncContacts(client *whatsmeow.Client) error {
	contacts, err := client.Store.Contacts.GetAllContacts(context.Background())
	if err != nil {
		return err
	}
	for jid, info := range contacts {
		if err := store.storeContact(client, jid, contactName(info, jid.User)); err != nil {
			return err
		}
	}
	return nil
}
