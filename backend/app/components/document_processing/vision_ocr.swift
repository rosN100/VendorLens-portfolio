import Foundation
import Vision
import AppKit

// Local macOS OCR: input is a temporary page image; output contains only observations.
guard CommandLine.arguments.count == 2,
      let image = NSImage(contentsOfFile: CommandLine.arguments[1]),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("ocr_input_unavailable\n", stderr); exit(2)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["en-US"]
request.usesLanguageCorrection = false
let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([request])
    var rows: [[String: Any]] = []
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let r = observation.boundingBox
        var words: [[String: Any]] = []
        let text = candidate.string
        for token in text.split(whereSeparator: { $0.isWhitespace }) {
            if let box = try? candidate.boundingBox(for: token.startIndex..<token.endIndex) {
                let b = box.boundingBox
                words.append(["text": String(token), "bbox": [b.minX, 1-b.maxY, b.maxX, 1-b.minY]])
            }
        }
        rows.append(["text": text, "confidence": candidate.confidence,
                     "bbox": [r.minX, 1-r.maxY, r.maxX, 1-r.minY], "words": words])
    }
    let output = try JSONSerialization.data(withJSONObject: rows, options: [.sortedKeys])
    FileHandle.standardOutput.write(output)
} catch {
    fputs("ocr_execution_failed\n", stderr); exit(3)
}
