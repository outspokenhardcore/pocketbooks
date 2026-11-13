import AVFoundation
import Combine
import MediaPlayer
import UIKit

/// Drives AVPlayer for HLS playback while a book is still generating, plus
/// chapter seeking, Now-Playing info and last-position sync to the server.
@MainActor
final class PlayerViewModel: ObservableObject {
    @Published var status: JobStatus?
    @Published var chapters: [Chapter] = []
    @Published var transcript: [TranscriptLine] = []
    @Published var currentTime: Double = 0
    @Published var isPlaying = false
    /// Seconds left on the sleep timer, or nil when it is off.
    @Published var sleepRemaining: Int?

    /// The book is playing from a local offline download rather than streaming.
    @Published private(set) var offline = false
    @Published private(set) var jobID: Int?
    /// True when an AVPlayer is attached and ready to play (offline or HLS).
    var isAttached: Bool { player != nil }
    /// Human-readable error surfaced to the UI (e.g. "Cannot reach server").
    @Published var lastError: String?

    let book: Book
    private let api: APIClient
    private let downloads = DownloadManager.shared
    private var player: AVPlayer?
    private var streamPath: String?
    private var timeObserver: Any?
    private var pollTask: Task<Void, Never>?
    private var sleepTask: Task<Void, Never>?
    private var lastSync = Date.distantPast
    private var lastTranscriptFetch = Date.distantPast
    private var coverImage: UIImage?
    /// Blocks position sync until restorePosition() has finished seeking.
    /// Without this, the first time-observer tick (currentTime = 0) overwrites
    /// the saved position on the server before we have a chance to seek to it.
    private var positionRestored = false

    init(api: APIClient, book: Book) { self.api = api; self.book = book }

    func start() {
        configureAudioSession()
        lastError = nil
        Task { await fetchCover() }
        // Offline-first: only treat as offline if the file is on disk AND non-empty.
        // isDownloaded() already enforces this, but we check explicitly here too so
        // a corrupt manifest entry can never send the player into a silent state.
        if downloads.isDownloaded(book.bookId) {
            offline = true
            downloads.markPlayed(book.bookId)
            attachPlayer(item: AVPlayerItem(url: downloads.fileURL(book.bookId)))
            if let lines = downloads.localTranscript(book.bookId) { transcript = lines }
            Task { await restorePosition() }
            return
        }
        // Stale manifest entry (file missing or 0-byte) — clear it so the UI
        // stops showing the green "offline" checkmark for this book.
        if downloadedHasManifestEntryOnly(book.bookId) {
            downloads.delete(book.bookId)
        }
        if api.settings.isCatalogue {
            Task { await startFromCatalogue() }
            return
        }
        Task {
            do {
                let voiceID = UserDefaults.standard.integer(forKey: "voiceID")
                let conv = try await api.convert(bookID: book.bookId, voiceID: voiceID)
                self.jobID = conv.jobId
                self.status = conv.status
                self.streamPath = conv.streamUrl
                if conv.status.playable { await attachStream() }
                await refreshTranscript(force: true)
                pollStatus()
            } catch let e as APIError {
                switch e {
                case .http(401): lastError = "Authentication failed"
                case .http(404): lastError = "Book not found on server"
                default: lastError = "Server error"
                }
            } catch {
                lastError = "Cannot reach server"
            }
        }
    }

    /// Play a pre-rendered book straight from static files. There is no job to
    /// create or poll: the audio is finished, the chapter marks and transcript
    /// are plain JSON next to it, and the position stays on this device.
    private func startFromCatalogue() async {
        let catalogue = CatalogueClient.shared
        guard let entry = catalogue.entry(book.bookId),
              let url = catalogue.url(entry.hlsUrl) else {
            lastError = "This book is not in the catalogue"
            return
        }
        status = JobStatus(
            jobId: 0, bookId: book.bookId, state: "done",
            doneChunks: entry.chapters, totalChunks: entry.chapters,
            availableSeconds: entry.durationS ?? 0, playable: true,
            streamUrl: entry.hlsUrl, hasM4b: entry.m4bUrl != nil,
            title: entry.title, author: entry.author)
        chapters = (try? await catalogue.chapters(for: entry)) ?? []
        transcript = (try? await catalogue.transcript(for: entry)) ?? []
        attachPlayer(item: AVPlayerItem(url: url))
        await restorePosition()
    }

    /// True when a download manifest entry exists but the file is missing or 0-byte.
    /// Means the previous download attempt didn't finish — manifest needs cleanup.
    private func downloadedHasManifestEntryOnly(_ bookID: String) -> Bool {
        guard FileManager.default.fileExists(atPath: downloads.fileURL(bookID).path)
              == false else { return false }
        return downloads.totalBytes == 0 || downloads.downloaded[bookID] != nil
    }

    /// Fetch the synced transcript (online). Throttled, and only replaces the
    /// current one when it grew, to avoid flicker while generation continues.
    private func refreshTranscript(force: Bool = false) async {
        guard let jid = jobID else { return }
        if !force, Date().timeIntervalSince(lastTranscriptFetch) < 8 { return }
        lastTranscriptFetch = Date()
        if let t = try? await api.transcript(jobID: jid), t.count >= transcript.count {
            transcript = t
        }
    }

    /// id (start time) of the line currently being spoken, for highlight/scroll.
    func currentLineID() -> Double? {
        guard !transcript.isEmpty else { return nil }
        var lo = 0, hi = transcript.count - 1, ans = 0
        while lo <= hi {                       // last line with startS <= currentTime
            let mid = (lo + hi) / 2
            if transcript[mid].startS <= currentTime { ans = mid; lo = mid + 1 }
            else { hi = mid - 1 }
        }
        return transcript[ans].id
    }

    private func attachStream() async {
        guard player == nil, let sp = streamPath, let url = api.absURL(sp) else { return }
        let asset = AVURLAsset(url: url, options: [
            "AVURLAssetHTTPHeaderFieldsKey":
                ["Authorization": "Bearer \(api.settings.token)"]
        ])
        attachPlayer(item: AVPlayerItem(asset: asset))
        await restorePosition()
    }

    /// Trigger an offline download of the finished M4B (enabled once available).
    func downloadForOffline() {
        guard let jid = jobID else { return }
        downloads.download(book: book, jobID: jid, api: api)
    }

    private func attachPlayer(item: AVPlayerItem) {
        let p = AVPlayer(playerItem: item)
        self.player = p
        timeObserver = p.addPeriodicTimeObserver(
            forInterval: CMTime(seconds: 1, preferredTimescale: 1),
            queue: .main
        ) { [weak self] t in
            guard let self else { return }
            Task { @MainActor in
                self.currentTime = t.seconds
                self.updateNowPlaying()
                self.maybeSyncPosition()
            }
        }
        setupRemoteCommands()
        play()
    }

    func play() { player?.play(); isPlaying = true }
    func pause() { player?.pause(); isPlaying = false; Task { await syncPosition(force: true) } }

    // MARK: - Sleep timer

    func startSleepTimer(minutes: Int) {
        sleepTask?.cancel()
        sleepRemaining = minutes * 60
        sleepTask = Task { [weak self] in
            while true {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if Task.isCancelled { return }
                guard let self else { return }
                let r = (self.sleepRemaining ?? 0) - 1
                if r <= 0 { self.pause(); self.sleepRemaining = nil; return }
                self.sleepRemaining = r
            }
        }
    }

    func cancelSleepTimer() {
        sleepTask?.cancel(); sleepTask = nil; sleepRemaining = nil
    }

    func seek(to seconds: Double) {
        player?.seek(to: CMTime(seconds: seconds, preferredTimescale: 600))
    }

    func seek(toChapter ch: Chapter) {
        if let s = ch.startS { seek(to: s) }
    }

    /// Playback duration: the finished item's length when known (offline M4B or
    /// a completed stream), otherwise how much audio has been generated so far.
    var duration: Double {
        if let d = player?.currentItem?.duration.seconds, d.isFinite, d > 0 {
            return d
        }
        return status?.availableSeconds ?? 0
    }

    /// index of the chapter currently playing (matches Chapter.id), for highlight.
    func currentChapterIndex() -> Int? {
        var best: Int?
        var bestStart = -1.0
        for ch in chapters {
            if let s = ch.startS, s <= currentTime, s >= bestStart {
                bestStart = s; best = ch.index
            }
        }
        return best
    }

    private func pollStatus() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            guard let self, let jid = self.jobID else { return }
            while !Task.isCancelled {
                if let st = try? await self.api.status(jobID: jid) {
                    self.status = st
                    if st.playable && self.player == nil { await self.attachStream() }
                    if self.chapters.isEmpty || self.chapters.contains(where: { $0.startS == nil }) {
                        self.chapters = (try? await self.api.chapters(jobID: jid)) ?? self.chapters
                    }
                    await self.refreshTranscript()
                    if st.state == "done" || st.state == "failed" {
                        await self.refreshTranscript(force: true)   // final full text
                        break
                    }
                }
                try? await Task.sleep(nanoseconds: 1_500_000_000)
            }
        }
    }

    // MARK: - Position sync (cross-device, last-write-wins on the server)

    private func restorePosition() async {
        positionRestored = false
        if let prog = try? await api.getProgress(bookID: book.bookId),
           prog.positionS > 1 {
            seek(to: prog.positionS)
        }
        positionRestored = true
    }

    private func maybeSyncPosition() {
        if Date().timeIntervalSince(lastSync) > 15 { Task { await syncPosition() } }
    }

    private func syncPosition(force: Bool = false) async {
        guard positionRestored || force else { return }
        guard force || Date().timeIntervalSince(lastSync) > 15 else { return }
        lastSync = Date()
        try? await api.putProgress(bookID: book.bookId,
                                   positionS: currentTime,
                                   durationS: status?.availableSeconds,
                                   title: book.title,
                                   author: status?.author ?? book.author)
    }

    // MARK: - System audio integration

    private func configureAudioSession() {
        let s = AVAudioSession.sharedInstance()
        try? s.setCategory(.playback, mode: .spokenAudio)
        try? s.setActive(true)
    }

    private func setupRemoteCommands() {
        let c = MPRemoteCommandCenter.shared()
        c.playCommand.addTarget { [weak self] _ in self?.play(); return .success }
        c.pauseCommand.addTarget { [weak self] _ in self?.pause(); return .success }
        c.skipForwardCommand.preferredIntervals = [30]
        c.skipBackwardCommand.preferredIntervals = [15]
        c.skipForwardCommand.addTarget { [weak self] _ in
            self?.seek(to: (self?.currentTime ?? 0) + 30); return .success }
        c.skipBackwardCommand.addTarget { [weak self] _ in
            self?.seek(to: max(0, (self?.currentTime ?? 0) - 15)); return .success }
    }

    private func fetchCover() async {
        guard let url = api.coverURL(book.bookId) else { return }
        var req = URLRequest(url: url)
        req.setValue("Bearer \(api.settings.token)", forHTTPHeaderField: "Authorization")
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let img = UIImage(data: data) else { return }
        coverImage = img
        updateNowPlaying()
    }

    private func updateNowPlaying() {
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: book.title,
            MPMediaItemPropertyArtist: status?.author ?? book.author,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: currentTime,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        if let dur = status?.availableSeconds, dur > 0 {
            info[MPMediaItemPropertyPlaybackDuration] = dur
        }
        if let img = coverImage {
            let artwork = MPMediaItemArtwork(boundsSize: img.size) { _ in img }
            info[MPMediaItemPropertyArtwork] = artwork
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    func stop() {
        pollTask?.cancel()
        sleepTask?.cancel()
        if let o = timeObserver { player?.removeTimeObserver(o) }
        Task { await syncPosition(force: true) }
        player?.pause(); player = nil
    }
}
