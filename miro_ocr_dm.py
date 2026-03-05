"""
Miroボードの指定セクションをGemini CLIで文字起こしし、Discord DMで送信するツール

使い方:
  python3 miro_ocr_dm.py <miro_board_url> <section_numbers>

例:
  python3 miro_ocr_dm.py "https://miro.com/app/board/uXjVG2v0xRw=/" "5,6"
  python3 miro_ocr_dm.py "https://miro.com/app/board/uXjVG2v0xRw=/" "1,2,3"
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from html import unescape
from typing import Dict, List, Optional, Tuple

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
TMPDIR = tempfile.gettempdir()


# ── Miro読み取り (Playwright + Miro SDK) ──

MIRO_GET_ITEMS_JS = """
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(10000);

  const items = await page.evaluate(async () => {
    const all = await miro.board.get();
    return all.map(item => ({
      id: item.id,
      type: item.type,
      content: item.content || item.title || item.plainText || item.text || '',
      x: item.x, y: item.y, width: item.width, height: item.height
    }));
  });

  console.log(JSON.stringify(items));
  await browser.close();
})();
"""

MIRO_SCREENSHOT_JS = """
const { chromium } = require('playwright');

(async () => {
  const url = process.argv[2];
  const viewports = JSON.parse(process.argv[3]);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

  await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 });
  await page.waitForTimeout(10000);

  for (const vp of viewports) {
    await page.evaluate(async (v) => {
      await miro.board.viewport.set({
        viewport: { x: v.x, y: v.y, width: v.width, height: v.height },
        padding: { top: 10, bottom: 10, left: 10, right: 10 }
      });
    }, vp);
    await page.waitForTimeout(4000);
    await page.screenshot({ path: vp.path });
  }

  await browser.close();
})();
"""


def get_miro_items(board_url: str) -> List[Dict]:
    """Miroボードの全アイテムを取得"""
    js_path = os.path.join(TMPDIR, "miro_get_items.js")
    with open(js_path, "w") as f:
        f.write(MIRO_GET_ITEMS_JS)

    result = subprocess.run(
        ["node", js_path, board_url],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"Error getting Miro items: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    return json.loads(result.stdout)


def html_to_text(html_str: str) -> str:
    """HTMLタグを除去してプレーンテキストに変換"""
    text = unescape(html_str)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</p>\s*<p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_sections(items: List[Dict]) -> Dict[int, Dict]:
    """アイテムからセクション番号を抽出してマッピング"""
    sections = {}
    pattern = re.compile(r"(\d+)\.\s*(.+?)[\s=]*$", re.MULTILINE)

    for item in items:
        content = html_to_text(item.get("content", ""))
        match = pattern.search(content)
        if match:
            num = int(match.group(1))
            title = match.group(2).strip().rstrip("=").strip()
            body = content[match.end():].strip()
            sections[num] = {
                "title": title,
                "body": body,
                "item": item,
                "has_text": len(body) > 10,
            }
    return sections


def take_section_screenshots(board_url: str, sections: Dict[int, Dict], target_nums: List[int]) -> Dict[int, List[str]]:
    """テキストが少ないセクションのスクリーンショットを撮影"""
    viewports = []
    screenshot_map = {}

    for num in target_nums:
        sec = sections.get(num)
        if not sec:
            continue

        item = sec["item"]
        y = item["y"]
        height = item["height"]

        # セクションの範囲に応じてスクリーンショットを分割
        chunk_height = 3500
        paths = []
        current_y = y - 500
        remaining = height + 1000

        i = 0
        while remaining > 0:
            path = os.path.join(TMPDIR, f"miro_sec{num}_{i}.png")
            viewports.append({
                "x": item["x"] - 2000,
                "y": int(current_y),
                "width": 4000,
                "height": int(min(chunk_height, remaining + 500)),
                "path": path
            })
            paths.append(path)
            current_y += chunk_height - 500
            remaining -= chunk_height - 500
            i += 1

        screenshot_map[num] = paths

    if not viewports:
        return {}

    js_path = os.path.join(TMPDIR, "miro_screenshot.js")
    with open(js_path, "w") as f:
        f.write(MIRO_SCREENSHOT_JS)

    result = subprocess.run(
        ["node", js_path, board_url, json.dumps(viewports)],
        capture_output=True, text=True, timeout=180
    )
    if result.returncode != 0:
        print(f"Warning: Screenshot error: {result.stderr}", file=sys.stderr)

    return screenshot_map


# ── Gemini CLI で文字起こし ──

def gemini_ocr(image_paths: List[str], section_title: str) -> str:
    """Gemini CLIで画像を文字起こし"""
    prompt = (
        f"この画像はLPのセクション「{section_title}」の部分です。"
        "画像内のテキストをすべて正確に文字起こししてください。"
        "装飾やレイアウトの説明は不要で、テキスト内容だけ出力してください。"
    )

    env = os.environ.copy()
    if GEMINI_API_KEY:
        env["GEMINI_API_KEY"] = GEMINI_API_KEY

    cmd = ["gemini", "-p", prompt, "--"] + image_paths
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)

    if result.returncode != 0:
        print(f"Gemini CLI error: {result.stderr[:500]}", file=sys.stderr)
        return ""

    # Geminiの出力からツール使用メッセージを除去
    lines = result.stdout.strip().split("\n")
    filtered = [l for l in lines if not l.startswith("I will read")]
    return "\n".join(filtered).strip()


# ── Discord DM送信 ──

def send_discord_dm(messages: List[str]) -> bool:
    """discord_dm_send.pyを使ってDM送信"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_dm_send.py")
    result = subprocess.run(
        ["python3", script] + messages,
        capture_output=True, text=True, timeout=60
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"DM send error: {result.stderr}", file=sys.stderr)
        return False
    return True


# ── メイン ──

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 miro_ocr_dm.py <miro_board_url> <section_numbers>")
        print('Example: python3 miro_ocr_dm.py "https://miro.com/app/board/xxx=/" "5,6"')
        sys.exit(1)

    board_url = sys.argv[1]
    target_nums = [int(n.strip()) for n in sys.argv[2].split(",")]

    print(f"[1/4] Miroボードからアイテム取得中...")
    items = get_miro_items(board_url)
    print(f"  → {len(items)}個のアイテムを取得")

    print(f"[2/4] セクション解析中...")
    sections = parse_sections(items)
    print(f"  → セクション: {sorted(sections.keys())}")

    missing = [n for n in target_nums if n not in sections]
    if missing:
        print(f"  ⚠ セクション {missing} が見つかりません")

    # テキストがないセクションはスクリーンショット→Gemini OCR
    needs_ocr = [n for n in target_nums if n in sections and not sections[n]["has_text"]]
    has_text = [n for n in target_nums if n in sections and sections[n]["has_text"]]

    screenshot_map = {}
    if needs_ocr:
        print(f"[3/4] セクション {needs_ocr} のスクリーンショット撮影 → Gemini OCRで文字起こし中...")
        screenshot_map = take_section_screenshots(board_url, sections, needs_ocr)

        for num in needs_ocr:
            paths = screenshot_map.get(num, [])
            if paths:
                title = sections[num]["title"]
                # Gemini APIレート制限対策
                if num != needs_ocr[0]:
                    print("  (レート制限回避のため30秒待機...)")
                    time.sleep(30)
                ocr_text = gemini_ocr(paths, title)
                if ocr_text:
                    sections[num]["body"] = ocr_text
                    sections[num]["has_text"] = True
                    print(f"  → セクション{num} 文字起こし完了")
                else:
                    print(f"  ⚠ セクション{num} の文字起こしに失敗")
    else:
        print(f"[3/4] 全セクションにテキストあり、OCRスキップ")

    print(f"[4/4] Discord DMで送信中...")
    messages = []
    for num in target_nums:
        if num not in sections:
            continue
        sec = sections[num]
        msg = f"【セクション{num}. {sec['title']}】\n\n{sec['body']}"
        messages.append(msg)

    if messages:
        send_discord_dm(messages)
        print("完了！")
    else:
        print("送信するメッセージがありません")


if __name__ == "__main__":
    main()
