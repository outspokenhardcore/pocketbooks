import Foundation

/// A catalogue of pre-rendered books served as plain files.
///
/// A demo needs no server: once a book has been generated, every byte the app
/// needs is a static file. `python -m app.catalogue export` writes the tree,
/// any web server hands it out, and this client reads it.
///
/// What a catalogue deliberately cannot do: start a conversion, sync listening
/// position across devices, or produce a recap. Those need a real server.
struct CatalogueEntry: Codable, Hashable {
    let bookId: String
    let title: String
    let author: String
    let durationS: Double?
    let chapters: Int
    let coverUrl: String?
    let chaptersUrl: String
    let transcriptUrl: String
    let hlsUrl: String
    let m4bUrl: String?

    enum CodingKeys: String, CodingKey {
        case bookId = "book_id", title, author, chapters
        case durationS = "duration_s", coverUrl = "cover_url"
        case chaptersUrl = "chapters_url", transcriptUrl = "transcript_url"
        case hlsUrl = "hls_url", m4bUrl = "m4b_url"
    }
}

struct CatalogueManifest: Codable {
    let version: Int
    let voiceId: Int
    let voiceName: String
    let books: [CatalogueEntry]

    enum CodingKeys: String, CodingKey {
        case version, books
        case voiceId = "voice_id", voiceName = "voice_name"
    }
}

enum CatalogueError: LocalizedError {
    case badURL
    case unsupportedVersion(Int)
    case empty

    var errorDescription: String? {
        switch self {
        case .badURL: return "That is not a valid catalogue address."
        case .unsupportedVersion(let v):
            return "This catalogue uses format \(v), which this app version cannot read."
        case .empty: return "The catalogue contains no books."
        }
    }
}

/// Loads and caches one catalogue. All URLs in the manifest are relative, so
/// the catalogue works under any path or domain it happens to be served from.
@MainActor
final class CatalogueClient {
    static let shared = CatalogueClient()

    /// Highest manifest version this build understands.
    static let supportedVersion = 1

    private(set) var base: URL?
    private(set) var entries: [String: CatalogueEntry] = [:]

    func entry(_ bookID: String) -> CatalogueEntry? { entries[bookID] }

    /// Resolve a manifest-relative path against the catalogue root.
    func url(_ relative: String?) -> URL? {
        guard let relative, let base else { return nil }
        return URL(string: relative, relativeTo: base)?.absoluteURL
    }

    /// Fetch the manifest and return its books in the app's own model.
    func load(from address: String) async throws -> [Book] {
        var text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.hasSuffix("/") { text += "/" }
        guard let root = URL(string: text) else { throw CatalogueError.badURL }

        let manifestURL = root.appendingPathComponent("catalogue.json")
        // The manifest can change while the app is open. Fetch it from the
        // server even when a previous response is still fresh in URLCache.
        var request = URLRequest(url: manifestURL, cachePolicy: .reloadIgnoringLocalCacheData)
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw APIError.http(http.statusCode)
        }
        let manifest = try JSONDecoder().decode(CatalogueManifest.self, from: data)
        guard manifest.version <= Self.supportedVersion else {
            throw CatalogueError.unsupportedVersion(manifest.version)
        }
        guard !manifest.books.isEmpty else { throw CatalogueError.empty }

        base = root
        entries = Dictionary(uniqueKeysWithValues: manifest.books.map { ($0.bookId, $0) })
        return manifest.books.map { $0.asBook() }
    }

    func chapters(for entry: CatalogueEntry) async throws -> [Chapter] {
        try await fetch(entry.chaptersUrl, as: ChaptersResponse.self).chapters
    }

    func transcript(for entry: CatalogueEntry) async throws -> [TranscriptLine] {
        try await fetch(entry.transcriptUrl, as: TranscriptResponse.self).lines
    }

    private func fetch<T: Decodable>(_ relative: String, as: T.Type) async throws -> T {
        guard let url = url(relative) else { throw CatalogueError.badURL }
        let (data, response) = try await URLSession.shared.data(from: url)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw APIError.http(http.statusCode)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

extension CatalogueEntry {
    /// Map a catalogue entry onto the model the rest of the app already uses,
    /// so library and player views need no special cases.
    func asBook() -> Book {
        Book(bookId: bookId,
             filename: title,
             relPath: title,
             folder: "",
             title: title,
             author: author,
             series: nil,
             hasCover: coverUrl != nil,
             estDurationS: durationS,
             cacheStatus: "ready",
             positionS: LocalProgressStore.shared.load()[bookId]?.positionS ?? 0,
             updatedAt: nil,
             pdfMissing: nil)
    }
}
