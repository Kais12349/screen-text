"""Offline OCR worker. All models and dependencies are beside the application."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parent
os.environ['TEMP'] = os.environ['TMP'] = str(ROOT / 'cache')


def group_rows(results):
    rows = []
    for box, text, score, *_ in sorted(results or [], key=lambda r: min(p[1] for p in r[0])):
        x0, y0 = min(p[0] for p in box), min(p[1] for p in box)
        x1, y1 = max(p[0] for p in box), max(p[1] for p in box)
        item = dict(x0=x0, y0=y0, x1=x1, y1=y1, text=text, score=score)
        center = (y0 + y1) / 2
        match = next((row for row in reversed(rows) if abs(center - row['cy']) <= .45 * min(y1-y0, row['height'])), None)
        if match is None:
            match = dict(items=[], cy=center, height=y1-y0)
            rows.append(match)
        match['items'].append(item)
    for row in rows:
        row['items'].sort(key=lambda r: r['x0'])
        row.update(x0=min(r['x0'] for r in row['items']), x1=max(r['x1'] for r in row['items']),
                   y0=min(r['y0'] for r in row['items']), y1=max(r['y1'] for r in row['items']))
    return sorted(rows, key=lambda r: r['cy'])


def looks_like_code(rows):
    lines = [' '.join(i['text'] for i in r['items']) for r in rows]
    pattern = r'(^\s*(def\s*\w+|class\s*\w+\s*\(|import\s+|from\s+\w+|@\w+\.|\w+\.\w+\())|[{};]\s*$|\w+_\w+\('
    strong = sum(bool(re.search(pattern, line)) for line in lines)
    fields = sum(bool(re.match(r'^\s*\w+\s*:\s*(?:str|int|float|bool|list|dict|set|tuple|Optional|Union)\b', line)) for line in lines)
    return strong >= 2 or (strong >= 1 and fields >= 2)


def plain_text(rows):
    lines = []
    for row in rows:
        line = ''
        previous = None
        for item in row['items']:
            if previous:
                gap = item['x0'] - previous['x1']
                adjacent_cjk = bool(re.search(r'[\u4e00-\u9fff，。；：！？]$', previous['text']) or re.match(r'[\u4e00-\u9fff，。；：！？]', item['text']))
                line += '' if adjacent_cjk and gap < row['height']*.3 else (' ' if gap < row['height'] * 2 else '  ')
            line += item['text']
            previous = item
        lines.append(line)
    return '\n'.join(lines)


def code_text(engine, image, rows):
    from PIL import Image, ImageOps
    import cv2
    import numpy as np
    # Crop whole visual rows so a comment and its URL cannot change places.
    gray = ImageOps.grayscale(image)
    if statistics.median(gray.resize((64, 64)).getdata()) < 128:
        gray = ImageOps.invert(gray)
    gray = ImageOps.autocontrast(gray)
    crops, bounds = [], []
    for row in rows:
        detected = ' '.join(item['text'] for item in row['items'])
        # A detector can drop a trailing quote or narrow symbol. Extend only
        # incomplete quoted rows, keeping the usual crop and positions stable.
        right_margin = row['height']*1.5 if any(detected.count(q) % 2 for q in ('"', "'")) else 1
        bounds.append((max(0, math.floor(row['x0'])-1), max(0, math.floor(row['y0'])-1),
                       min(image.width, math.ceil(row['x1'] + right_margin)), min(image.height, math.ceil(row['y1'])+1)))
        crop = gray.crop(bounds[-1])
        crop = crop.resize((crop.width*3, crop.height*3), Image.Resampling.LANCZOS).convert('RGB')
        crops.append(np.asarray(crop))
    recognized, _ = engine.text_rec(crops, True)
    positioned, pitch_samples, span_samples = [], [], []
    for row, bound, result in zip(rows, bounds, recognized):
        text, score, info = result
        columns, words, positions, states, confidences = info
        characters = [(c, bound[0] + (pos+.5)*(bound[2]-bound[0])/columns)
                      for word, locations in zip(words, positions) for c, pos in zip(word, locations)]
        for (a, xa), (b, xb) in zip(characters, characters[1:]):
            if a.isascii() and b.isascii() and a.isalnum() and b.isalnum():
                pitch_samples.append(xb-xa)
        # CTC positions are quantized. Measuring a run avoids rounding a four-space
        # indent to three because its most common single-character step is too large.
        for start in range(len(characters)-4):
            run = characters[start:start+5]
            if all(c.isascii() and c.isalnum() for c, _ in run):
                span_samples.append((run[-1][1]-run[0][1])/4)
        positioned.append((characters, text, score))
    # The measured spacing, rather than guessed Python syntax, restores missing spaces.
    pitch = statistics.median(span_samples or pitch_samples) if (span_samples or pitch_samples) else statistics.median(r['height'] for r in rows)*.5
    pitch = max(pitch, 1)
    _, foreground = cv2.threshold(np.asarray(gray), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink_lefts, bars_by_row, cursors_by_row = [], [], []
    for row, bound in zip(rows, bounds):
        mask = foreground[bound[1]:bound[3], bound[0]:bound[2]]
        _, _, components, centers = cv2.connectedComponentsWithStats(mask)
        ink = [(box, center) for box, center in zip(components[1:], centers[1:]) if box[4] >= 4]
        ink_lefts.append(bound[0] + min((box[0] for box, _ in ink), default=0))
        bars_by_row.append([bound[0] + center[0] for box, center in ink
                            if box[2] <= max(2, pitch*.35) and box[3] >= pitch*1.6
                            and box[4] >= box[3]*.65])
        # An outlined I-beam has a detached central stroke and four small caps.
        # These disconnected caps distinguish it from a printed I, l, 1, or |.
        cursors = []
        for box, center in ink:
            x, y, w, h, area = box
            if w > pitch*.3 or not pitch*1.2 <= h <= pitch*2.2: continue
            caps = set()
            for cap in components[1:]:
                cx, cy, cw, ch, ca = cap
                if ca < 2 or cw > pitch*.4 or ch > pitch*.35: continue
                dx = cx + cw/2 - center[0]
                if not pitch*.15 <= abs(dx) <= pitch*.6: continue
                if abs(cy-y) <= pitch*.3: caps.add(('top', dx > 0))
                if abs(cy+ch-y-h) <= pitch*.3: caps.add(('bottom', dx > 0))
            if len(caps) >= 3: cursors.append(bound[0] + center[0])
        cursors_by_row.append(cursors)
    left = min(ink_lefts)
    deltas = [b['cy']-a['cy'] for a,b in zip(rows, rows[1:]) if b['cy'] > a['cy']]
    line_pitch = statistics.median(deltas) if deltas else rows[0]['height']*1.4
    lines, scores = [], []
    previous_row = None
    for row, ink_left, bars, cursors, (characters, fallback, score) in zip(rows, ink_lefts, bars_by_row, cursors_by_row, positioned):
        output = ''
        previous = None
        quote = None
        comment = False
        escaped = False
        # Recover a vertical stroke from pixels only, rather than replacing every
        # I/l/1 in identifiers, strings, and numeric literals.
        characters = [(c, x) for c, x in characters
                      if not (c in 'I1l工|丨｜' and any(abs(x-cursor) < pitch*.7 for cursor in cursors))]
        if cursors and not any(not c.isspace() for c, _ in characters): continue
        if previous_row:
            lines.extend([''] * min(8, max(0, round((row['cy']-previous_row['cy'])/line_pitch)-1)))
        for bar_x in bars:
            nearest = min(range(len(characters)), key=lambda i: abs(characters[i][1]-bar_x)) if characters else None
            if nearest is not None and abs(characters[nearest][1]-bar_x) < pitch*.7:
                if characters[nearest][0] in 'I1l|丨｜': characters[nearest] = ('|', bar_x)
            elif nearest is not None:
                characters.append(('|', bar_x))
        characters.sort(key=lambda item: item[1])
        had_space = False
        for c, x in characters:
            if c.isspace():
                had_space = True
                continue
            if previous:
                a, xa = previous
                # CJK glyphs occupy about two Latin cells; retain their natural adjacency.
                expected = pitch * ((1 if a.isascii() or a in '（）［］｛｝，：；' else 2)
                                    + (1 if c.isascii() or c in '（）［］｛｝，：；' else 2)) / 2
                # Decoder positions can drift by a fraction of a character cell.
                # Bias toward the smaller gap to avoid inventing a second space.
                missing = math.floor((x-xa-expected)/pitch + .25)
                boundary = (a.isalnum() and c.isalnum() and a.isascii() and c.isascii()) or (a == '#' and c.isalnum()) or (a.isalnum() and c == '{')
                if had_space and (not a.isascii() or not c.isascii()):
                    output += ' '
                elif (boundary or had_space) and x-xa > expected + pitch*.6 and missing > 0:
                    output += ' ' * min(missing, 16)
            if not comment and quote is None:
                c = c.translate(str.maketrans('（）［］｛｝，：；', '()[]{},:;'))
                if c == '#': comment = True
                elif c in ('"', "'"): quote = c
            elif quote:
                if c == quote and not escaped: quote = None
            escaped = c == '\\' and not escaped
            output += c
            previous = (c, x)
            had_space = False
        indent = min(160, max(0, round((ink_left-left)/pitch)))
        output = re.sub(r'\b(https?)\s*:\s*/\s*/\s*', r'\1://', output.strip() or fallback)
        lines.append(' '*indent + output)
        scores.append(score)
        previous_row = row
    return '\n'.join(lines), scores


def recognize(input_path):
    from PIL import Image, ImageOps
    from rapidocr_onnxruntime import RapidOCR
    image = ImageOps.exif_transpose(Image.open(input_path)).convert('RGB')
    engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1, max_side_len=3200)
    results, _ = engine(image, use_cls=True, text_score=.65)
    rows = group_rows(results)
    if not rows:
        return dict(text='', language='中英文', error=None, code=False)
    is_code = looks_like_code(rows)
    if is_code:
        text, scores = code_text(engine, image, rows)
    else:
        text, scores = plain_text(rows), [i['score'] for r in rows for i in r['items']]
    return dict(text=text, language='中英文', error=None, code=is_code,
                low_confidence=sum(score < .92 for score in scores))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = recognize(args.input)
    except Exception as exc:
        result = dict(text='', language='', error='本地识别失败：' + str(exc))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
