import Foundation
import Vision
import AppKit
let path = CommandLine.arguments[1]
guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(2) }
let W = Double(cg.width), H = Double(cg.height)
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false
req.recognitionLanguages = ["en-US"]
try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
var out: [[String: Any]] = []
for o in (req.results ?? []) {
  guard let c = o.topCandidates(1).first else { continue }
  let s = c.string
  var idx = s.startIndex
  // split into whitespace-separated words with per-word boxes
  for w in s.split(separator: " ", omittingEmptySubsequences: true) {
    guard let r = s.range(of: w, range: idx..<s.endIndex) else { continue }
    idx = r.upperBound
    if let b = try? c.boundingBox(for: r) {
      let bb = b.boundingBox
      out.append(["text": String(w), "xyxy": [bb.minX*W, (1-bb.maxY)*H, bb.maxX*W, (1-bb.minY)*H]])
    }
  }
}
let d = try JSONSerialization.data(withJSONObject: out)
print(String(data: d, encoding: .utf8)!)
