import Foundation

struct Book: Identifiable, Codable, Hashable {
    var id: String { bookId }
    let bookId: String
    let filename: String
    let relPath: String
    let folder: String
    let title: String
    let author: String
    let series: String?
    let hasCover: Bool
    let estDurationS: Double?
    let cacheStatus: String   // none | cleaned | partial | ready
    let positionS: Double
    let updatedAt: Double?    // present in the "started" listing
    /// True when the progress entry exists on the server but the PDF is currently
    /// not found in the library (moved, deleted, NAS offline…). The book stays
    /// visible in the Listening tab so the user knows it's there.
    let pdfMissing: Bool?

    enum CodingKeys: String, CodingKey {
        case bookId = "book_id", filename, title, author, series
        case relPath = "rel_path", folder
        case hasCover = "has_cover", estDurationS = "est_duration_s"
        case cacheStatus = "cache_status", positionS = "position_s"
        case updatedAt = "updated_at", pdfMissing = "pdf_missing"
    }

    var isUnavailable: Bool { pdfMissing == true }

    /// Fraction listened, when we have an estimated total runtime.
    var fractionComplete: Double? {
        guard let est = estDurationS, est > 0, positionS > 1 else { return nil }
        return min(positionS / est, 1)
    }
}

struct BooksResponse: Codable { let books: [Book] }

struct Voice: Identifiable, Codable, Hashable {
    let id: Int
    let name: String
}

struct LibraryFolder: Identifiable, Codable, Hashable {
    var id: String { path }
    let name: String
    let path: String
    let count: Int
}

/// What the connected server is willing to do. A public demo server turns off
/// anything that spends money or CPU for a stranger.
struct ServerFeatures: Codable, Equatable {
    var recap: Bool = true
}

/// Response of GET /api/library — a folder's contents, or global search results.
struct BrowseResponse: Codable {
    let folder: String
    let subfolders: [LibraryFolder]
    let books: [Book]
    let voices: [Voice]
    /// Absent on older servers, which always supported every feature.
    let features: ServerFeatures?
}

struct JobStatus: Codable {
    let jobId: Int
    let bookId: String?
    let state: String          // queued | processing | done | failed | ...
    let doneChunks: Int
    let totalChunks: Int
    let availableSeconds: Double
    let playable: Bool
    let streamUrl: String
    let hasM4b: Bool
    let title: String?
    let author: String?

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id", bookId = "book_id", state
        case doneChunks = "done_chunks", totalChunks = "total_chunks"
        case availableSeconds = "available_seconds", playable
        case streamUrl = "stream_url", hasM4b = "has_m4b", title, author
    }

    var progress: Double {
        totalChunks > 0 ? Double(doneChunks) / Double(totalChunks) : 0
    }
}

struct ConvertResponse: Codable {
    let jobId: Int
    let bookId: String
    let streamUrl: String
    let status: JobStatus
    enum CodingKeys: String, CodingKey {
        case jobId = "job_id", bookId = "book_id", streamUrl = "stream_url", status
    }
}

struct Chapter: Codable, Identifiable, Hashable {
    var id: Int { index }
    let index: Int
    let title: String
    let startS: Double?
    enum CodingKeys: String, CodingKey {
        case index, title, startS = "start_s"
    }
}

struct ChaptersResponse: Codable { let chapters: [Chapter] }

struct TranscriptLine: Codable, Identifiable, Hashable {
    let startS: Double
    let endS: Double
    let text: String
    let chapter: Int
    var id: Double { startS }   // start times are unique & increasing
    enum CodingKeys: String, CodingKey {
        case startS = "start_s", endS = "end_s", text, chapter
    }
}

struct TranscriptResponse: Codable { let lines: [TranscriptLine] }

struct RecapStart: Codable {
    let recapId: String
    enum CodingKeys: String, CodingKey { case recapId = "recap_id" }
}

/// Response from POST /api/jobs/{id}/cancel. The server returns the new
/// `status` string (usually "cancelled", but for an idempotent cancel of an
/// already-finished job it could be "done" or "failed").
struct CancelResponse: Codable {
    let jobId: Int
    let status: String
    enum CodingKeys: String, CodingKey {
        case jobId = "job_id", status
    }
}

struct RecapStatus: Codable {
    let state: String          // processing | ready | failed
    let text: String?
    let hasAudio: Bool
    let error: String?
    let audioUrl: String?
    enum CodingKeys: String, CodingKey {
        case state, text, error
        case hasAudio = "has_audio", audioUrl = "audio_url"
    }
}

struct Progress: Codable {
    let bookId: String
    let positionS: Double
    let durationS: Double?
    let updatedAt: Double
    enum CodingKeys: String, CodingKey {
        case bookId = "book_id", positionS = "position_s"
        case durationS = "duration_s", updatedAt = "updated_at"
    }
}
