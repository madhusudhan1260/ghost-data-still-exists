// Local OCR helper using Apple's Vision framework (macOS only).
// Usage: swift vision_ocr.swift <image.png>
// Prints recognized text lines, one per line, top-to-bottom then left-to-right.
import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count > 1 else { exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let img = NSImage(contentsOf: url),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { exit(3) }
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.usesLanguageCorrection = false
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
try handler.perform([req])
var items: [(Double, Double, String)] = []
for obs in (req.results ?? []) {
    if let top = obs.topCandidates(1).first {
        let b = obs.boundingBox
        items.append((1.0 - Double(b.maxY), Double(b.minX), top.string))
    }
}
items.sort { (a, b) in
    if abs(a.0 - b.0) > 0.008 { return a.0 < b.0 }
    return a.1 < b.1
}
for it in items { print(String(format: "%.3f\t%.3f\t%@", it.0, it.1, it.2)) }
