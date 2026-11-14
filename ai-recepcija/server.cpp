// Glasnik AI – mali HTTP server u C++17 (bez vanjskih biblioteka, samo POSIX).
// Poslužuje index.html i prima upite iz kontakt forme (POST /api/kontakt).
// Upiti se dopisuju u upiti.jsonl (jedan JSON objekt po retku).
//
// Build:  g++ -std=c++17 -O2 -pthread -o glasnik server.cpp
// Run:    ./glasnik [port]        (zadano 8080)

#include <arpa/inet.h>
#include <netinet/in.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>

static const size_t MAX_HEADER = 16 * 1024;
static const size_t MAX_BODY = 16 * 1024;
static const char* INDEX_FILE = "index.html";
static const char* LOG_FILE = "upiti.jsonl";

static std::mutex g_log_mutex;

// ---------- pomocne funkcije ----------

static std::string url_decode(const std::string& s) {
    std::string out;
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '+') {
            out += ' ';
        } else if (s[i] == '%' && i + 2 < s.size() && isxdigit((unsigned char)s[i + 1]) &&
                   isxdigit((unsigned char)s[i + 2])) {
            out += (char)std::strtol(s.substr(i + 1, 2).c_str(), nullptr, 16);
            i += 2;
        } else {
            out += s[i];
        }
    }
    return out;
}

static std::map<std::string, std::string> parse_form(const std::string& body) {
    std::map<std::string, std::string> m;
    std::stringstream ss(body);
    std::string pair;
    while (std::getline(ss, pair, '&')) {
        auto eq = pair.find('=');
        if (eq == std::string::npos) continue;
        m[url_decode(pair.substr(0, eq))] = url_decode(pair.substr(eq + 1));
    }
    return m;
}

static std::string json_escape(const std::string& s) {
    std::string out;
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += (char)c;
                }
        }
    }
    return out;
}

static std::string lower(std::string s) {
    for (auto& c : s) c = (char)tolower((unsigned char)c);
    return s;
}

static bool valid_email(const std::string& e) {
    auto at = e.find('@');
    return at != std::string::npos && at > 0 && e.find('.', at) != std::string::npos &&
           e.size() < 200 && e.find(' ') == std::string::npos;
}

static void send_all(int fd, const std::string& data) {
    size_t sent = 0;
    while (sent < data.size()) {
        ssize_t n = send(fd, data.data() + sent, data.size() - sent, MSG_NOSIGNAL);
        if (n <= 0) return;
        sent += (size_t)n;
    }
}

static void respond(int fd, int code, const std::string& status, const std::string& type,
                    const std::string& body) {
    std::ostringstream r;
    r << "HTTP/1.1 " << code << " " << status << "\r\n"
      << "Content-Type: " << type << "\r\n"
      << "Content-Length: " << body.size() << "\r\n"
      << "X-Content-Type-Options: nosniff\r\n"
      << "Connection: close\r\n\r\n"
      << body;
    send_all(fd, r.str());
}

static void respond_json(int fd, int code, const std::string& status, const std::string& json) {
    respond(fd, code, status, "application/json; charset=utf-8", json);
}

static bool read_file(const std::string& path, std::string& out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    std::ostringstream ss;
    ss << f.rdbuf();
    out = ss.str();
    return true;
}

// ---------- obrada zahtjeva ----------

static void handle_contact(int fd, const std::string& body) {
    auto form = parse_form(body);
    auto get = [&](const char* k) {
        auto it = form.find(k);
        std::string v = it == form.end() ? "" : it->second;
        if (v.size() > 2000) v.resize(2000);
        return v;
    };
    std::string ime = get("ime"), tvrtka = get("tvrtka"), email = get("email");
    if (ime.empty() || tvrtka.empty() || !valid_email(email)) {
        respond_json(fd, 400, "Bad Request", "{\"ok\":false,\"error\":\"Provjerite ime, tvrtku i e-mail.\"}");
        return;
    }
    char ts[32];
    std::time_t now = std::time(nullptr);
    std::strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&now));

    std::ostringstream line;
    line << "{\"vrijeme\":\"" << ts << "\",\"ime\":\"" << json_escape(ime) << "\",\"tvrtka\":\""
         << json_escape(tvrtka) << "\",\"email\":\"" << json_escape(email) << "\",\"tel\":\""
         << json_escape(get("tel")) << "\",\"interes\":\"" << json_escape(get("interes"))
         << "\",\"poruka\":\"" << json_escape(get("poruka")) << "\"}\n";
    {
        std::lock_guard<std::mutex> lock(g_log_mutex);
        std::ofstream out(LOG_FILE, std::ios::app);
        if (!out) {
            respond_json(fd, 500, "Internal Server Error", "{\"ok\":false,\"error\":\"Spremanje nije uspjelo.\"}");
            return;
        }
        out << line.str();
    }
    std::cout << "[upit] " << tvrtka << " <" << email << ">" << std::endl;
    respond_json(fd, 200, "OK", "{\"ok\":true}");
}

static void handle_client(int fd) {
    timeval tv{10, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);

    std::string buf;
    char tmp[4096];
    size_t header_end = std::string::npos;
    while (header_end == std::string::npos && buf.size() < MAX_HEADER) {
        ssize_t n = recv(fd, tmp, sizeof tmp, 0);
        if (n <= 0) { close(fd); return; }
        buf.append(tmp, (size_t)n);
        header_end = buf.find("\r\n\r\n");
    }
    if (header_end == std::string::npos) {
        respond(fd, 431, "Request Header Fields Too Large", "text/plain", "Zahtjev prevelik");
        close(fd);
        return;
    }

    std::string head = buf.substr(0, header_end);
    std::string body = buf.substr(header_end + 4);

    std::istringstream hs(head);
    std::string method, target, version;
    hs >> method >> target >> version;
    auto q = target.find('?');
    std::string path = q == std::string::npos ? target : target.substr(0, q);

    size_t content_length = 0;
    std::string line;
    std::getline(hs, line);  // ostatak prvog retka
    while (std::getline(hs, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        auto colon = line.find(':');
        if (colon == std::string::npos) continue;
        if (lower(line.substr(0, colon)) == "content-length")
            content_length = (size_t)std::strtoul(line.c_str() + colon + 1, nullptr, 10);
    }

    if (method == "GET" && (path == "/" || path == "/index.html")) {
        std::string html;
        if (read_file(INDEX_FILE, html))
            respond(fd, 200, "OK", "text/html; charset=utf-8", html);
        else
            respond(fd, 500, "Internal Server Error", "text/plain", "index.html nije pronaden (pokrenite server iz foldera stranice).");
    } else if (method == "POST" && path == "/api/kontakt") {
        if (content_length > MAX_BODY) {
            respond_json(fd, 413, "Payload Too Large", "{\"ok\":false,\"error\":\"Poruka je preduga.\"}");
        } else {
            while (body.size() < content_length) {
                ssize_t n = recv(fd, tmp, sizeof tmp, 0);
                if (n <= 0) break;
                body.append(tmp, (size_t)n);
                if (body.size() > MAX_BODY) break;
            }
            if (body.size() < content_length)
                respond_json(fd, 400, "Bad Request", "{\"ok\":false,\"error\":\"Nepotpun zahtjev.\"}");
            else
                handle_contact(fd, body.substr(0, content_length));
        }
    } else if (method == "GET" && path == "/healthz") {
        respond(fd, 200, "OK", "text/plain", "ok");
    } else {
        respond(fd, 404, "Not Found", "text/plain; charset=utf-8", "404 - stranica ne postoji");
    }
    close(fd);
}

int main(int argc, char** argv) {
    int port = argc > 1 ? std::atoi(argv[1]) : 8080;
    signal(SIGPIPE, SIG_IGN);

    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { perror("socket"); return 1; }
    int yes = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((uint16_t)port);
    if (bind(srv, (sockaddr*)&addr, sizeof addr) < 0) { perror("bind"); return 1; }
    if (listen(srv, 64) < 0) { perror("listen"); return 1; }

    std::cout << "Glasnik AI radi na http://localhost:" << port << std::endl;
    for (;;) {
        int fd = accept(srv, nullptr, nullptr);
        if (fd < 0) continue;
        std::thread(handle_client, fd).detach();
    }
}
