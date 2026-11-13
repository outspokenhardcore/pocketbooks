import SwiftUI
import UIKit

/// Small in-memory cache so a cover is fetched once per session.
private final class CoverCache {
    static let shared = CoverCache()
    private let cache = NSCache<NSURL, UIImage>()
    func image(for url: URL) -> UIImage? { cache.object(forKey: url as NSURL) }
    func set(_ img: UIImage, for url: URL) { cache.setObject(img, forKey: url as NSURL) }
}

/// Like AsyncImage, but sends the bearer token so the protected `/cover`
/// endpoint returns the image instead of 401. SwiftUI's built-in AsyncImage
/// can't attach an Authorization header, which is why covers showed blank.
struct AuthImage<Placeholder: View>: View {
    let url: URL?
    let token: String
    @ViewBuilder var placeholder: () -> Placeholder
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image).resizable()
            } else {
                placeholder()
            }
        }
        .task(id: url) { await load() }
    }

    private func load() async {
        guard let url, image == nil else { return }
        if let cached = CoverCache.shared.image(for: url) { image = cached; return }
        var req = URLRequest(url: url, timeoutInterval: 10)
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let img = UIImage(data: data) else { return }
        CoverCache.shared.set(img, for: url)
        image = img
    }
}
