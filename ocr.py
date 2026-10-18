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


def looks_like_markup(line):
    # Detection may merge a tag name with its first attribute or miss the
    # closing angle bracket. Keep those rows in the position-aware code path.
    return bool(re.match(r'^\s*<(?:/?[A-Za-z][\w:.-]*(?:\s|[/>=]|$)|!--|!DOCTYPE\b)', line, re.I))


def looks_like_code(rows):
    lines = [' '.join(i['text'] for i in r['items']) for r in rows]
    pattern = r'(^\s*(def\s*\w+|class\s*\w+\s*\(|import\s+|from\s+\w+|@\w+\.|\w+\.\w+\())|[{};]\s*$|\w+_\w+\('
    strong = sum(bool(re.search(pattern, line)) for line in lines)
    fields = sum(bool(re.match(r'^\s*\w+\s*:\s*(?:str|int|float|bool|list|dict|set|tuple|Optional|Union)\b', line)) for line in lines)
    # Assignment-only snippets have no class/def header. The first pass may
    # miss an equals sign; repeated pairs of dotted identifiers still identify
    # this layout without treating prose or a lone address as source code.
    identifier = r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+'
    assignments = sum(bool(re.fullmatch(r'\s*' + identifier + r'(?:\s*[=三一]\s*|\s+)' + identifier + r'\s*', line)) for line in lines)
    js_object = any(re.match(r'^\s*(?:const|let|var)\s+[\w$]+\s*=\s*\{', line) for line in lines)
    properties = sum(bool(re.match(r'^\s*[A-Za-z_$][\w$]*\s*:', line)) for line in lines)
    return (any(looks_like_markup(line) for line in lines) or strong >= 2
            or (strong >= 1 and fields >= 2) or assignments >= 2 or (js_object and properties >= 2))


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


def recover_code_symbols(engine, gray, rows, bounds, positioned, pitch):
    """Find short symbol rows using glyphs in this image, never bracket balance."""
    import cv2
    import numpy as np

    height = statistics.median(r['height'] for r in rows)
    threshold, mask = cv2.threshold(np.asarray(gray), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Remove editor guides, underlines and selection outlines, which otherwise
    # join a brace to a rectangle. Printed glyphs are shorter than these lines.
    clean = cv2.subtract(mask, cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                        np.ones((max(3, round(height*1.05)), 1), np.uint8)))
    clean = cv2.subtract(clean, cv2.morphologyEx(clean, cv2.MORPH_OPEN,
                        np.ones((1, max(3, round(pitch*2.2))), np.uint8)))
    # Faint selection borders can be broken at each text baseline. Remove their
    # low-contrast straight strokes even when they are shorter than a full row.
    weak = ((np.asarray(gray) >= threshold-10) & (mask > 0)).astype('uint8')*255
    weak_lines = cv2.morphologyEx(weak, cv2.MORPH_OPEN,
                                np.ones((max(3, round(height*.7)), 1), np.uint8))
    clean = cv2.subtract(clean, weak_lines)
    cleaned_gray = np.asarray(gray).copy()
    cleaned_gray[(mask > 0) & (clean == 0)] = 255
    _, labels, stats, _ = cv2.connectedComponentsWithStats(clean)
    glyphs = [(i, tuple(map(int, box))) for i, box in enumerate(stats[1:], 1)
              if box[4] >= 4 and box[2] <= height*1.4 and box[3] <= height*1.2]

    def shape(glyph):
        i, (x, y, w, h, _) = glyph
        return cv2.resize((labels[y:y+h, x:x+w] == i).astype('float32'), (12, 28))

    templates, quote_templates = [], []
    for row, bound, (characters, _, score) in zip(rows, bounds, positioned):
        for char, px in characters:
            if char == "'" and score >= .9:
                nearby = [g for g in glyphs if g[1][3] <= height*.35 and g[1][2] <= pitch*.4
                          and abs(g[1][0]+g[1][2]/2-px) < pitch*.6
                          and row['y0']-2 <= g[1][1] <= row['cy']-height*.15]
                if nearby:
                    quote_templates.append(shape(min(nearby, key=lambda g: abs(g[1][0]+g[1][2]/2-px))))
            if char not in '{}()[]' or score < .9: continue
            nearby = [g for g in glyphs if abs(g[1][1]+g[1][3]/2-row['cy']) < height*.4
                      and abs(g[1][0]+g[1][2]/2-px) < pitch*.55 and g[1][3] >= height*.65]
            if not nearby: continue
            glyph = min(nearby, key=lambda g: abs(g[1][0]+g[1][2]/2-px))
            template = shape(glyph)
            templates.append((char, template, glyph[1][2]/glyph[1][3]))
            mirror = dict(zip('{}()[]', '}{)(]['))[char]
            templates.append((mirror, template[:, ::-1], glyph[1][2]/glyph[1][3]))

    def classify(glyph, limit=.23):
        _, (_, _, w, h, _) = glyph
        if h < height*.65 or not templates: return None
        candidate = shape(glyph)
        # Square brackets also have a distinctive straight stem and two caps.
        # They may have no correctly decoded exemplar anywhere in the image.
        if .15 <= w/h <= .6 and min(candidate[:3].mean(), candidate[-3:].mean()) > .75:
            middle = candidate[6:-6]
            if middle[:, :3].mean() > .65 and middle[:, -4:].mean() < .1:
                return '[', .94
            if middle[:, -3:].mean() > .65 and middle[:, :4].mean() < .1:
                return ']', .94
        matches = [(float(np.mean(np.abs(candidate-t))) + abs(w/h-ratio)*.5, char)
                   for char, t, ratio in templates]
        distance, char = min(matches)
        if distance >= limit: return None
        if char in '{}':
            # A round parenthesis bends broadly; a brace has a narrow central
            # notch. This also works when there is no decoded parenthesis to
            # use as a template elsewhere in this particular screenshot.
            i, (x, y, _, _, _) = glyph
            pixels = labels[y:y+h, x:x+w] == i
            midpoints = [float(np.flatnonzero(line).mean()) for line in pixels if line.any()]
            end_count = max(1, h//5)
            ends = statistics.mean(midpoints[:end_count] + midpoints[-end_count:])
            middle = statistics.mean(midpoints[h//2-1:h//2+2])
            # Straight diagonal slashes have no central bend.
            if abs(middle-ends) < w*.18: return None
            char = '{' if middle < ends else '}'
            edges = [np.flatnonzero(line)[0 if char == '{' else -1]
                     for line in pixels if line.any()]
            extreme = min(edges) if char == '{' else max(edges)
            broad = sum(abs(edge-extreme) <= w*.2 for edge in edges)/len(edges)
            if char == '}' and broad >= .5 and distance > .15: char = ')'
        return char, 1-distance

    # Build additional rows only from real, matched punctuation components.
    extra = []
    for glyph in sorted(glyphs, key=lambda g: g[1][1]):
        _, (x, y, w, h, _) = glyph
        cy = y+h/2
        if any(abs(cy-r['cy']) < height*.6 for r in rows): continue
        match = classify(glyph)
        if match is None: continue
        row = next((r for r in extra if abs(r['cy']-cy) < height*.4), None)
        if row is None:
            row = dict(cy=cy, symbols=[], height=height)
            extra.append(row)
        row['symbols'].append((match[0], x+w/2, match[1]))
    if not extra:
        return rows, bounds, positioned, None

    originals = {id(row): (row, bound, result) for row, bound, result in zip(rows, bounds, positioned)}
    bright = (np.asarray(gray) < threshold*.85).astype('uint8')
    _, _, bright_stats, _ = cv2.connectedComponentsWithStats(bright)
    recovered = []
    for row in sorted(rows + extra, key=lambda r: r['cy']):
        old = 'items' in row
        members = [g for g in glyphs if abs(g[1][1]+g[1][3]/2-row['cy']) < height*.45
                   and (not old or (row['x0']-height*1.5 <= g[1][0] <= row['x1']+height*1.5
                        and (g[1][0] >= row['x0']-pitch*.9 or g[1][2] > max(2,pitch*.3))))]
        if not members:
            if old: recovered.append(originals[id(row)])
            continue
        if not old and any(g[1][3] >= height*.4 and classify(g) is None for g in members):
            continue
        x0, y0 = min(g[1][0] for g in members), min(g[1][1] for g in members)
        x1, y1 = max(g[1][0]+g[1][2] for g in members), max(g[1][1]+g[1][3] for g in members)
        bound = (max(0,x0-2), max(0,y0-2), min(gray.width,x1+2), min(gray.height,y1+2))
        row = dict(row, x0=x0, y0=y0, x1=x1, y1=y1)
        if not old:
            # Tiny commas are disconnected from the main closing brace.
            symbols = [(c, x) for c, x, _ in row['symbols']]
            small = [g[1] for g in members if g[1][3] < height*.4 and g[1][2] <= pitch*.6]
            for gx, gy, gw, gh, _ in small:
                if gy <= row['cy']: continue
                has_dot = any(abs(sx+sw/2-gx-gw/2) < pitch*.35 and sy < row['cy']
                              for sx,sy,sw,sh,_ in small)
                symbols.append((';' if has_dot else ',', gx+gw/2))
            symbols.sort(key=lambda p:p[1])
            text = ''.join(c for c, _ in symbols)
            recovered.append((row, bound, (symbols, text, min(s[2] for s in row['symbols']))))
        else:
            from PIL import Image
            crop = Image.fromarray(cleaned_gray).crop(bound)
            result = engine.text_rec([np.asarray(crop.resize((crop.width*3,crop.height*3)).convert('RGB'))], True)[0][0]
            text, score, (columns, words, positions, _, _) = result
            chars = [(c, bound[0]+(pos+.5)*(bound[2]-bound[0])/columns)
                     for word, locations in zip(words,positions) for c,pos in zip(word,locations)]
            # An outlined mouse I-beam has detached bright dots above and below
            # the baseline. A diagonal fragment beside its stem can still prove
            # that the obscured character is a slash, even if OCR calls it M/1.
            caps = [tuple(map(int, b)) for b in bright_stats[1:]
                    if b[4] >= 2 and b[2] <= pitch*.45 and b[3] <= height*.35
                    and abs(b[1]+b[3]/2-row['cy']) < height]
            for ax, ay, aw, ah, _ in caps:
                if ay > row['cy']-height*.35: continue
                for bx, by, bw, bh, _ in caps:
                    cx = ax+aw/2
                    if not (height*.9 < by-ay < height*1.5
                            and abs(bx+bw/2-cx) <= 1.5
                            and by > row['cy']+height*.25): continue
                    stem = mask[ay+ah:by, max(0,round(cx)-1):round(cx)+1]
                    if not stem.size or np.mean(np.any(stem > 0,axis=1)) < .65: continue
                    for fragment in members:
                        gi, (fx, fy, fw, fh, _) = fragment
                        if not (height*.3 <= fh < height*.75 and fw <= pitch*.6
                                and cx-pitch < fx+fw/2 < cx-pitch*.25): continue
                        ys, xs = np.where(labels[fy:fy+fh,fx:fx+fw] == gi)
                        slope, intercept = np.polyfit(ys+fy, xs+fx, 1)
                        if not -.8 < slope < -.15: continue
                        slash_x = slope*(row['cy']-1)+intercept
                        chars = [(c,x) for c,x in chars if abs(x-slash_x) >= pitch*.65]
                        chars.append(('/',float(slash_x)))
            # Trust matched boundary glyphs over the recognizer's guess (often
            # a dot or a digit). Do not rewrite letters or symbols inside strings.
            for glyph in members:
                match = classify(glyph, .34)
                _, (gx, gy, gw, gh, _) = glyph
                if (match is None and quote_templates and text.count("'") % 2
                        and gh <= height*.35 and gw <= pitch*.4
                        and gy <= y0+height*.25 and gx > x1-pitch):
                    distance = min(float(np.mean(np.abs(shape(glyph)-t))) for t in quote_templates)
                    if distance < .36: match = ("'", 1-distance)
                if match is None: continue
                px = gx+gw/2
                if px < x1-pitch*1.2: continue
                chars = [(c,x) for c,x in chars if abs(x-px) >= pitch*.65]
                chars.append((match[0],px))
            recovered.append((row, bound, (chars, text, score)))
    if not any('symbols' in r[0] for r in recovered):
        return rows, bounds, positioned, None
    return ([r[0] for r in recovered], [r[1] for r in recovered], [r[2] for r in recovered], clean)


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
        # incomplete quoted/tag rows. Read the pixels beyond the detection box;
        # do not synthesize a closing tag or infer it from syntax.
        incomplete_tag = looks_like_markup(detected) and not detected.rstrip().endswith('>')
        right_margin = row['height']*1.5 if incomplete_tag or any(detected.count(q) % 2 for q in ('"', "'")) else 1
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
    rows, bounds, positioned, recovered_mask = recover_code_symbols(engine, gray, rows, bounds, positioned, pitch)
    if recovered_mask is not None:
        foreground = recovered_mask
    ink_lefts, bars_by_row, cursors_by_row = [], [], []
    for row, bound in zip(rows, bounds):
        mask = foreground[bound[1]:bound[3], bound[0]:bound[2]]
        _, labels, components, centers = cv2.connectedComponentsWithStats(mask)
        ink = [(box, center) for box, center in zip(components[1:], centers[1:]) if box[4] >= 4]
        bars_by_row.append([bound[0] + center[0] for box, center in ink
                            if box[2] <= max(2, pitch*.35) and box[3] >= pitch*1.6
                            and box[4] >= box[3]*.65])
        # An outlined I-beam has a detached central stroke and four small caps.
        # These disconnected caps distinguish it from a printed I, l, 1, or |.
        cursors = []
        for component_id, box in enumerate(components[1:], 1):
            x, y, w, h, area = box
            if area < 4 or w > pitch*.7 or not pitch*1.2 <= h <= pitch*2.2: continue
            # Some cursor caps connect to the stem after resampling. Measure
            # its middle rather than requiring the entire component be narrow.
            middle = labels[y+h//3:y+2*h//3, x:x+w] == component_id
            core = np.flatnonzero(np.count_nonzero(middle, axis=0) >= middle.shape[0]*.8)
            if not len(core) or core[-1]-core[0]+1 > pitch*.3: continue
            stem_x = x + (core[0]+core[-1]+1)/2
            caps = set()
            for cap in components[1:]:
                cx, cy, cw, ch, ca = cap
                if ca < 2 or cw > pitch*.4 or ch > pitch*.35: continue
                dx = cx + cw/2 - stem_x
                if not pitch*.15 <= abs(dx) <= pitch*.6: continue
                if abs(cy-y) <= pitch*.3: caps.add(('top', dx > 0))
                if abs(cy+ch-y-h) <= pitch*.3: caps.add(('bottom', dx > 0))
            if len(caps) >= 2 and {end for end, _ in caps} == {'top', 'bottom'}:
                cursors.append(bound[0] + stem_x)
        cursors_by_row.append(cursors)
        # A mouse cursor to the left of a line must not shift every other line's
        # indentation. Ignore only the cursor's own stem and detached caps.
        text_ink = [box for box, _ in ink if not any(
            box[0]+bound[0] >= cursor-pitch*.7 and box[0]+box[2]+bound[0] <= cursor+pitch*.6
            for cursor in cursors)]
        ink_lefts.append(bound[0] + min((box[0] for box in text_ink), default=0))
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
                      if not (c in 'TI1l工|丨｜' and any(abs(x-cursor) < pitch*.7 for cursor in cursors))]
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
                boundary = ((a.isalnum() and c.isalnum() and a.isascii() and c.isascii())
                            or (a == '#' and c.isalnum()) or (a.isalnum() and c == '{')
                            or a in ':,'
                            or a in '=<>!+*/%&|^-' or c in '=<>!+*/%&|^-')
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
