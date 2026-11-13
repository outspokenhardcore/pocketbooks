import Foundation
import Combine

/// Offline downloads (Audible-style): a finished book's M4B is saved to the
/// device so it plays without the server and survives server-side cache
/// eviction. The listening position still syncs to the server, so deleting a
/// local copy never loses your place.
@MainActor
final class DownloadManager: ObservableObject {
    static let shared = DownloadManager()

    /// book_id -> on-disk metadata for downloaded books.
    @Published private(set) var downloaded: [String: DownloadedBook] = [:]
    /// book_id -> 0...1 while a download is in flight.
    @Published private(set) var progress: [String: Double] = [:]

    /// Allows external download delegates to report progress.
    func setProgress(_ frac: Double, for bookID: String) {
        progress[bookID] = frac
    }

    /// Soft cap on local storage; oldest (by lastPlayed) is evicted past this.
    var maxLocalBytes: Int64 = 4 * 1024 * 1024 * 1024  // 4 GB

    private var tasks: [String: URLSessionDownloadTask] = [:]

    struct DownloadedBook: Codable {
        let bookID: String
        let title: String
        let author: String
        let sizeBytes: Int64
        var lastPlayed: Date
    }

    private init() { load(); cleanupStaleEntries() }

    /// Remove any manifest entries whose on-disk file is missing or not a
    /// valid audio file (0-byte stubs, HTTP-404 bodies). Runs once at startup
    /// so stale entries from the old broken downloads are cleared automatically.
    private func cleanupStaleEntries() {
        let stale = downloaded.keys.filter { !isDownloaded($0) }
        for id in stale { delete(id) }
        if !stale.isEmpty {
            print("DownloadManager: cleaned \(stale.count) stale entries")
        }
    }

    // MARK: - Paths

    private var dir: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Audiobooks", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }
    private var manifestURL: URL { dir.appendingPathComponent("manifest.json") }
    func fileURL(_ bookID: String) -> URL { dir.appendingPathComponent("\(bookID).m4b") }
    func transcriptURL(_ bookID: String) -> URL {
        dir.appendingPathComponent("\(bookID).transcript.json")
    }

    /// The synced "lyrics" transcript saved alongside an offline download.
    func localTranscript(_ bookID: String) -> [TranscriptLine]? {
        guard let data = try? Data(contentsOf: transcriptURL(bookID)) else { return nil }
        return try? JSONDecoder().decode([TranscriptLine].self, from: data)
    }

    /// True when a valid audio file exists on disk (M4B or MP3 magic bytes).
    /// Rejects 0-byte stubs AND HTTP-error bodies (404 JSON etc.) from ever
    /// appearing as "downloaded" in the UI.
    func isDownloaded(_ bookID: String) -> Bool {
        guard downloaded[bookID] != nil else { return false }
        let url = fileURL(bookID)
        guard FileManager.default.fileExists(atPath: url.path) else { return false }
        // Reject 0-byte and 404-body stubs before they can mislead the UI.
        guard let data = try? Data(contentsOf: url, options: .mappedIfSafe),
              data.count >= 12 else { return false }
        return isValidAudioHeader(data)
    }

    /// Returns true if the first bytes of `data` match a known audio format.
    /// M4B: bytes 4-7 = "ftyp" (after 4-byte atom size). MP3: 0xFF 0xEB, or
    /// ID3 tag at offset 0 ("ID3"). All other patterns are invalid.
    private func isValidAudioHeader(_ data: Data) -> Bool {
        // M4B: "....ftyp" — atom header is size(4) + "ftyp"(4) at offset 0
        if data.count >= 12 {
            let m4b = data.subdata(in: 4..<8)
            if m4b == Data([0x66, 0x74, 0x79, 0x70]) { return true } // "ftyp"
        }
        // MP3 with ID3v2 tag: starts with "ID3"
        if data.count >= 3 {
            let h = data.prefix(3)
            if h == Data([0x49, 0x44, 0x33]) { return true }           // "ID3"
        }
        // MP3 without ID3: first frame sync word 0xFFF? or 0xFFE?
        if data.count >= 2 {
            let w = UInt16(data[0]) << 8 | UInt16(data[1])
            if w & 0xFFE0 == 0xFFE0 { return true }
        }
        return false
    }

    var totalBytes: Int64 { downloaded.values.reduce(0) { $0 + $1.sizeBytes } }

    /// Synthesize Book objects from the local manifest for offline display.
    /// Skips entries whose file is missing or not a valid audio file.
    func offlineBooks() -> [Book] {
        downloaded.values.compactMap { dl -> Book? in
            guard isDownloaded(dl.bookID) else { return nil }
            let local = LocalProgressStore.shared.load()[dl.bookID]
            return Book(
                bookId: dl.bookID,
                filename: "",
                relPath: "",
                folder: "",
                title: dl.title,
                author: dl.author,
                series: nil,
                hasCover: false,
                estDurationS: local?.durationS,
                cacheStatus: "ready",
                positionS: local?.positionS ?? 0,
                updatedAt: local?.updatedAt,
                pdfMissing: false
            )
        }.sorted { ($0.updatedAt ?? 0) > ($1.updatedAt ?? 0) }
    }

    // MARK: - Download

    // MARK: - Downloads

    /// Download from the streaming MP3 endpoint (growing file during generation).
    /// The connection stays open until the server finishes, then the file is
    /// moved into offline storage automatically. No progress callback since
    /// the total size is unknown until the end.
    func downloadStreamingMP3(bookID: String, jobID: Int,
                               api: APIClient, onDone: @escaping () -> Void) {
        guard let url = api.absURL("/api/jobs/\(jobID)/mp3") else { return }
        var req = URLRequest(url: url)
        req.setValue("Bearer \(api.settings.token)",
                     forHTTPHeaderField: "Authorization")

        let session = URLSession(
            configuration: .default,
            delegate: StreamingDownloadHandler(
                bookID: bookID,
                onComplete: { [weak self] tempURL in
                    guard let self, let tempURL else { return }
                    self.importDownloadedMP3(bookID: bookID, from: tempURL)
                    DispatchQueue.main.async { onDone() }
                }),
            delegateQueue: nil)
        let task = session.downloadTask(with: req)
        task.resume()
    }

    func download(book: Book, jobID: Int, api: APIClient) {
        guard !isDownloaded(book.bookId), tasks[book.bookId] == nil else { return }
        guard let url = api.absURL("/api/jobs/\(jobID)/audio") else { return }
        var req = URLRequest(url: url)
        req.setValue("Bearer \(api.settings.token)", forHTTPHeaderField: "Authorization")

        progress[book.bookId] = 0
        let delegate = DownloadDelegate(bookID: book.bookId) { [weak self] frac in
            Task { @MainActor in self?.progress[book.bookId] = frac }
        } onFinish: { [weak self] tempURL in
            Task { @MainActor in self?.finish(book: book, tempURL: tempURL) }
        }
        let session = URLSession(configuration: .default, delegate: delegate, delegateQueue: nil)
        let task = session.downloadTask(with: req)
        tasks[book.bookId] = task
        task.resume()

        // Save the synced transcript too, so the lyrics view works offline.
        Task {
            if let lines = try? await api.transcript(jobID: jobID),
               let data = try? JSONEncoder().encode(lines) {
                try? data.write(to: transcriptURL(book.bookId))
            }
        }
    }

    private func finish(book: Book, tempURL: URL?) {
        defer { tasks[book.bookId] = nil; progress[book.bookId] = nil }
        guard let tempURL else { return }
        let dest = fileURL(book.bookId)
        try? FileManager.default.removeItem(at: dest)
        do { try FileManager.default.moveItem(at: tempURL, to: dest) }
        catch { return }
        let size = (try? FileManager.default.attributesOfItem(atPath: dest.path)[.size] as? Int64) ?? nil
        downloaded[book.bookId] = DownloadedBook(
            bookID: book.bookId, title: book.title, author: book.author,
            sizeBytes: size ?? 0, lastPlayed: Date())
        save()
        enforceBudget(keep: book.bookId)
    }

    // MARK: - Maintenance

    func markPlayed(_ bookID: String) {
        guard var b = downloaded[bookID] else { return }
        b.lastPlayed = Date(); downloaded[bookID] = b; save()
    }

    func delete(_ bookID: String) {
        try? FileManager.default.removeItem(at: fileURL(bookID))
        try? FileManager.default.removeItem(at: transcriptURL(bookID))
        downloaded[bookID] = nil; save()
    }

    /// Import a downloaded MP3 file into offline storage. Called after the
    /// streaming download completes. Registers the book in the offline manifest.
    func importDownloadedMP3(bookID: String, from tempURL: URL) {
        let dest = fileURL(bookID)
        try? FileManager.default.createDirectory(
            at: dest.deletingLastPathComponent(),
            withIntermediateDirectories: true)
        // Atomically move from temp, replacing any previous download
        _ = try? FileManager.default.replaceItemAt(dest, withItemAt: tempURL)
        let size = (try? tempURL.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
        downloaded[bookID] = DownloadedBook(
            bookID: bookID, title: "", author: "",
            sizeBytes: Int64(size), lastPlayed: Date())
        save()
    }

    /// Evict least-recently-played local books until under the soft cap.
    private func enforceBudget(keep: String?) {
        var total = totalBytes
        guard total > maxLocalBytes else { return }
        for b in downloaded.values.sorted(by: { $0.lastPlayed < $1.lastPlayed }) {
            if b.bookID == keep { continue }
            delete(b.bookID)
            total -= b.sizeBytes
            if total <= maxLocalBytes { break }
        }
    }

    // MARK: - Persistence

    private func load() {
        guard let data = try? Data(contentsOf: manifestURL),
              let list = try? JSONDecoder().decode([DownloadedBook].self, from: data)
        else { return }
        downloaded = Dictionary(uniqueKeysWithValues: list.map { ($0.bookID, $0) })
    }

    private func save() {
        let list = Array(downloaded.values)
        if let data = try? JSONEncoder().encode(list) { try? data.write(to: manifestURL) }
    }
}

/// Minimal URLSessionDownloadDelegate that captures the completed download
/// location. Used for streaming MP3 downloads where total size is unknown.
final class StreamingDownloadHandler: NSObject, URLSessionDownloadDelegate {
    let bookID: String
    let onComplete: (URL?) -> Void

    init(bookID: String, onComplete: @escaping (URL?) -> Void) {
        self.bookID = bookID
        self.onComplete = onComplete
    }

    func urlSession(_ session: URLSession,
                    downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try? FileManager.default.moveItem(at: location, to: tmp)
        onComplete(tmp)
        session.finishTasksAndInvalidate()
    }

    func urlSession(_ session: URLSession,
                    task: URLSessionTask,
                    didCompleteWithError error: Error?) {
        if let error {
            print("Streaming download error for \(bookID): \(error)")
            onComplete(nil)
        }
    }
}

/// URLSession delegate bridging download progress + completion to the manager.
private final class DownloadDelegate: NSObject, URLSessionDownloadDelegate {
    let bookID: String
    let onProgress: (Double) -> Void
    let onFinish: (URL?) -> Void

    init(bookID: String, onProgress: @escaping (Double) -> Void,
         onFinish: @escaping (URL?) -> Void) {
        self.bookID = bookID; self.onProgress = onProgress; self.onFinish = onFinish
    }

    func urlSession(_ s: URLSession, downloadTask: URLSessionDownloadTask,
                    didWriteData _: Int64, totalBytesWritten w: Int64,
                    totalBytesExpectedToWrite e: Int64) {
        if e > 0 { onProgress(Double(w) / Double(e)) }
    }

    func urlSession(_ s: URLSession,
                    downloadTask: URLSessionDownloadTask,
                    didFinishDownloadingTo location: URL) {
        // Only accept 2xx responses. HTTP 404/500 from a still-generating job
        // must not create a stub file that the manifest later reports as "ready".
        guard let http = downloadTask.response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode) else {
            print("DownloadDelegate: non-2xx \(downloadTask.response?.description ?? "?") for \(bookID) -- ignoring")
            onFinish(nil)
            return
        }
        // Move synchronously out of the temp location before the closure returns.
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".m4b")
        try? FileManager.default.moveItem(at: location, to: tmp)
        onFinish(tmp)
        s.finishTasksAndInvalidate()
    }

    func urlSession(_ s: URLSession,
                    task: URLSessionTask,
                    didCompleteWithError err: Error?) {
        if err != nil { onFinish(nil) }
    }
}
