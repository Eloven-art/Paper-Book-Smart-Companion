from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parent.parent
sample = ROOT / 'data/samples/demo_book'
manifest = json.loads((ROOT / 'scripts/reader_sample_manifest.json').read_text(encoding='utf-8'))
meta = json.loads((sample / 'book.json').read_text(encoding='utf-8'))
preview_sample = ROOT / '前端/assets/preview/demo_book'
for folder in ('pages', 'audio', 'video'):
    shutil.copytree(sample / folder, preview_sample / folder, dirs_exist_ok=True)
target = ROOT / 'data/books/demo_book'
if not target.exists():
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(sample, target)
    print('Updated demo book installed.')
    raise SystemExit(0)
if (target / 'book.json').is_file():
    current = json.loads((target / 'book.json').read_text(encoding='utf-8'))
    use_sample_videos = len(current.get('pages', [])) == len(meta['pages']) and all(
        not page.get('video_meta') or page['video_meta'].get('provider') == 'local_kenburns'
        for page in current.get('pages', [])) and all(
        (target / rel).is_file() and hashlib.sha256((target / rel).read_bytes()).hexdigest() in
        (old_hash, hashlib.sha256((sample / rel).read_bytes()).hexdigest())
        for rel, old_hash in manifest.items())
    changed = False
    for page in current.get('pages', []):
        source = next((p for p in meta['pages'] if p['page_no'] == page['page_no']), None)
        if not source or page.get('page_image') != source['page_image']:
            continue
        image = target / page['page_image']
        new_image = sample / source['page_image']
        if not image.is_file():
            continue
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest not in (manifest.get(source['page_image']), hashlib.sha256(new_image.read_bytes()).hexdigest()):
            print('Skipped customized sample page', page['page_no'])
            continue
        backup = image.with_suffix('.before-reader-update.png')
        if not backup.exists():
            shutil.copy2(image, backup)
        shutil.copy2(new_image, image)
        page['ocr_text'] = source['ocr_text']
        for obj in page.get('objects', []):
            match = next((o for o in source['objects'] if o['name'] == obj['name'] and o['bbox'] == obj['bbox']), None)
            if match:
                obj['contours'] = match.get('contours', [])
        # Only use the bundled local sample clips; do not replace real generated clips.
        if use_sample_videos:
            for key in ('video_clip_id', 'time_range', 'video_meta', 'narration_audio', 'keyframes'):
                page[key] = source.get(key)
        changed = True
    if changed:
        backup = target / 'book.before-reader-update.json'
        if not backup.exists():
            shutil.copy2(target / 'book.json', backup)
        if use_sample_videos:
            for folder in ('audio', 'video'):
                old_folder = target / folder
                backup_folder = target / (folder + '.before-reader-update')
                if old_folder.exists() and not backup_folder.exists():
                    shutil.copytree(old_folder, backup_folder)
                shutil.copytree(sample / folder, old_folder, dirs_exist_ok=True)
            current['video'] = meta.get('video')
        temp = target / 'book.reader-update.tmp'
        temp.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.replace(target / 'book.json')
        print('Reader sample upgraded. Original metadata and images backed up.')
