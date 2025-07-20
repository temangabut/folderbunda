import os
import requests
import json
import datetime
import time
import random
import re
import markdown
import google.generativeai as genai

# --- Konfigurasi ---
# URL API WORDPRESS SUMBER (tempat artikel diambil)
API_SOURCE_URL = "https://kisah69.blog/wp-json/wp/v2/posts"
# URL API WORDPRESS TARGET (tempat artikel akan diposting)
WP_TARGET_API_URL = "https://cerita.mywp.info/wp-json/wp/v2/posts"

STATE_FILE = 'artikel_terbit.json' # File untuk melacak postingan yang sudah diterbitkan
RANDOM_IMAGES_FILE = 'random_images.json' # File untuk URL gambar acak

# --- Konfigurasi Gemini API ---
GEMINI_API_KEY_CONTENT = os.getenv("GEMINI_API_KEY_CONTENT")
if not GEMINI_API_KEY_CONTENT:
    raise ValueError("GEMINI_API_KEY_CONTENT environment variable not set. Please set it in your GitHub Secrets or local environment.")

GEMINI_API_KEY_TITLE = os.getenv("GEMINI_API_KEY_TITLE")
if not GEMINI_API_KEY_TITLE:
    raise ValueError("GEMINI_API_KEY_TITLE environment variable not set. Please set it in your GitHub Secrets or local environment.")

# Buat model dengan API Key masing-masing menggunakan client_options
gemini_model_content = genai.GenerativeModel(
    "gemini-1.5-flash",
    client_options={"api_key": GEMINI_API_KEY_CONTENT}
)
gemini_model_title = genai.GenerativeModel(
    "gemini-1.5-flash",
    client_options={"api_key": GEMINI_API_KEY_TITLE}
)

# --- Konfigurasi Kredensial WordPress Target ---
WP_USERNAME = os.getenv('WP_USERNAME')
WP_APP_PASSWORD = os.getenv('WP_APP_PASSWORD')
if not WP_USERNAME or not WP_APP_PASSWORD:
    raise ValueError("WP_USERNAME and WP_APP_PASSWORD environment variables not set for target WordPress. Please set them in GitHub Secrets.")

# --- Penggantian Kata Khusus ---
REPLACEMENT_MAP = {
    "memek": "serambi lempit",
    "kontol": "rudal",
    "ngentot": "menggenjot",
    "vagina": "serambi lempit",
    "penis": "rudal",
    "seks": "bercinta",
    "mani": "kenikmatan",
    "sex": "bercinta"
}

# === Utilitas ===

def extract_first_image_url(html_content):
    """
    Mencari URL gambar pertama di dalam konten HTML.
    """
    match = re.search(r'<img[^>]+src="([^"]+)"', html_content, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

def strip_html_and_divs(html):
    """
    Menghapus sebagian besar tag HTML, kecuali yang esensial,
    dan mengganti </p> dengan dua newline untuk pemisahan paragraf.
    """
    html_with_newlines = re.sub(r'</p>', r'\n\n', html, flags=re.IGNORECASE)
    html_no_images = re.sub(r'<img[^>]*>', '', html_with_newlines)
    html_no_divs = re.sub(r'</?div[^>]*>', '', html_no_images, flags=re.IGNORECASE)
    clean_text = re.sub('<[^<]+?>', '', html_no_divs)
    clean_text = re.sub(r'\n{3,}', r'\n\n', clean_text).strip()
    return clean_text

def remove_anchor_tags(html_content):
    """Menghapus tag <a> tapi mempertahankan teks di dalamnya."""
    return re.sub(r'<a[^>]*>(.*?)<\/a>', r'\1', html_content)

def sanitize_filename(title):
    """Membersihkan judul agar cocok untuk nama file."""
    clean_title = re.sub(r'[^\w\s-]', '', title).strip().lower()
    return re.sub(r'[-\s]+', '-', clean_title)

def replace_custom_words(text):
    """Menerapkan penggantian kata khusus pada teks."""
    processed_text = text
    sorted_replacements = sorted(REPLACEMENT_MAP.items(), key=lambda item: len(item[0]), reverse=True)
    for old_word, new_word in sorted_replacements:
        pattern = re.compile(re.escape(old_word), re.IGNORECASE)
        processed_text = pattern.sub(new_word, processed_text)
    return processed_text

def edit_title_with_gemini(original_title):
    """
    Mengirim judul artikel ke Gemini AI untuk diedit agar lebih menarik/unik.
    Menggunakan model khusus judul.
    """
    print(f"🤖 Memulai pengeditan judul dengan Gemini AI (Model Judul): '{original_title}'...")
    try:
        prompt = (
            f"Ganti judul berikut agar menjadi lebih menarik, tidak vulgar, dan tetap relevan dengan topik cerita dewasa. "
            f"Sertakan elemen clickbait yang memancing rasa penasaran tanpa mengurangi keamanan konten.\n\n"
            f"Judul asli: '{original_title}'\n\n"
            f"Judul baru:"
        )
        response = gemini_model_title.generate_content(prompt)
        edited_title = response.text.strip()

        if edited_title.startswith('"') and edited_title.endswith('"'):
            edited_title = edited_title[1:-1]
            
        print(f"✅ Gemini AI (Model Judul) selesai mengedit judul. Hasil: '{edited_title}'")
        return edited_title
    except Exception as e:
        print(f"❌ Error saat mengedit judul dengan Gemini AI (Model Judul): {e}. Menggunakan judul asli.")
        return original_title


def edit_first_300_words_with_gemini(post_id, post_title, full_text_content):
    """
    Mengirim 300 kata pertama ke Gemini AI untuk diedit,
    dan menggabungkannya kembali dengan sisa artikel,
    mempertahankan format paragraf sisa artikel.
    Menggunakan model khusus konten.
    """
    words = full_text_content.split()

    if len(words) < 50:
        print(f"[{post_id}] Artikel terlalu pendek (<50 kata) untuk diedit oleh Gemini AI. Melewati pengeditan.")
        return full_text_content

    # Menghitung karakter untuk 300 kata pertama, termasuk spasi
    char_count_for_300_words = 0
    word_count = 0
    for i, word in enumerate(words):
        if word_count < 300:
            char_count_for_300_words += len(word)
            if i < len(words) - 1: # Tambahkan 1 untuk spasi antar kata
                char_count_for_300_words += 1
            word_count += 1
        else:
            break
            
    # Pastikan char_count_for_300_words tidak melebihi panjang string asli
    char_count_for_300_words = min(char_count_for_300_words, len(full_text_content))

    first_300_words_original_string = full_text_content[:char_count_for_300_words].strip()
    rest_of_article_text = full_text_content[char_count_for_300_words:].strip()

    print(f"🤖 Memulai pengeditan Gemini AI (Model Konten) untuk artikel ID: {post_id} - '{post_title}' ({len(first_300_words_original_string.split())} kata pertama)...")

    try:
        prompt = (
            f"Cerita Berikut adalah cuplikan dari 300 kata pertama dari cerita utuhnya "
            f"Paraphrase signifikan setiap kata, sehingga 300 kata pertama ini beda dari aslinya, gunakan bahasa informal dan lugas :\n\n"
            f"{first_300_words_original_string}"
        )

        response = gemini_model_content.generate_content(prompt)
        edited_text_from_gemini = response.text

        print(f"✅ Gemini AI (Model Konten) selesai mengedit bagian pertama artikel ID: {post_id}.")

        cleaned_edited_text = strip_html_and_divs(edited_text_from_gemini)

        final_combined_text = cleaned_edited_text.strip() + "\n\n" + rest_of_article_text.strip()

        return strip_html_and_divs(final_combined_text) # Pastikan output bersih dan diformat dengan benar

    except Exception as e:
        print(f"❌ Error saat mengedit dengan Gemini AI (Model Konten) untuk artikel ID: {post_id} - {e}. Menggunakan teks asli untuk bagian ini.")
        return full_text_content

# --- Fungsi untuk memuat dan menyimpan status postingan yang sudah diterbitkan ---
def load_published_posts_state():
    """Memuat ID postingan yang sudah diterbitkan dari file state."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                print(f"Warning: {STATE_FILE} is corrupted or empty. Starting with an empty published posts list.")
                return set()
    return set()

def save_published_posts_state(published_ids):
    """Menyimpan ID postingan yang sudah diterbitkan ke file state."""
    with open(STATE_FILE, 'w') as f:
        json.dump(list(published_ids), f)

def load_image_urls(file_path):
    """
    Memuat daftar URL gambar dari file JSON.
    """
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            try:
                urls = json.load(f)
                if isinstance(urls, list) and all(isinstance(url, str) for url in urls):
                    print(f"✅ Berhasil memuat {len(urls)} URL gambar dari '{file_path}'.")
                    return urls
                else:
                    print(f"❌ Error: Konten '{file_path}' bukan daftar string URL yang valid.")
                    return []
            except json.JSONDecodeError:
                print(f"❌ Error: Gagal mengurai JSON dari '{file_path}'. Pastikan formatnya benar.")
                return []
    else:
        print(f"⚠️ Peringatan: File '{file_path}' tidak ditemukan. Tidak ada gambar acak yang akan ditambahkan.")
        return []

def get_random_image_url(image_urls):
    """
    Memilih URL gambar secara acak dari daftar.
    """
    if image_urls:
        return random.choice(image_urls)
    return None

# === Fungsi untuk Mengirim Post ke WordPress Target ===
def publish_post_to_wordpress(wp_api_url, title, content_html, username, app_password, random_image_url=None, post_status='publish'):
    """
    Menerbitkan postingan ke WordPress REST API.
    """
    print(f"🚀 Menerbitkan '{title}' ke WordPress: {wp_api_url}...")

    final_content_for_wp = content_html
    if random_image_url:
        image_html = f'<p><img src="{random_image_url}" alt="{title}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;"></p>'
        final_content_for_wp = image_html + "\n\n" + content_html
        print(f"🖼️ Gambar acak '{random_image_url}' ditambahkan ke artikel.")

    post_data = {
        'title': title,
        'content': final_content_for_wp,
        'status': post_status,
        # 'categories': [1, 2], # Opsional: ID kategori jika ada
        # 'tags': [10, 20], # Opsional: ID tag jika ada
    }

    auth = requests.auth.HTTPBasicAuth(username, app_password)
    headers = {
        'Content-Type': 'application/json'
    }

    try:
        response = requests.post(wp_api_url, headers=headers, json=post_data, auth=auth)
        response.raise_for_status()

        result = response.json()
        print(f"✅ Artikel '{title}' berhasil diterbitkan ke WordPress! URL: {result.get('link')}")
        return result
    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP Error saat memposting ke WordPress: {e}")
        print(f"Respons server: {response.text}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ Terjadi kesalahan koneksi ke WordPress API: {e}")
        return None
    except json.JSONDecodeError:
        print(f"❌ Gagal mendekode JSON dari respons WordPress: {response.text}")
        return None
    except Exception as e:
        print(f"❌ Terjadi kesalahan tak terduga saat memposting ke WordPress: {e}")
        return None


# === Ambil semua postingan dari WordPress Self-Hosted REST API (SUMBER) ===
def fetch_raw_posts():
    """
    Mengambil semua postingan mentah (ID, judul, konten, tanggal) dari WordPress sumber
    tanpa pengeditan atau pembersihan mendalam.
    """
    all_posts_data = []
    page = 1
    per_page_limit = 100

    print(f"📥 Mengambil semua artikel mentah dari WordPress SUMBER: {API_SOURCE_URL}...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    while True:
        params = {
            'per_page': per_page_limit,
            'page': page,
            'status': 'publish',
            '_fields': 'id,title,content,date' # Hanya ambil bidang yang relevan
        }
        try:
            res = requests.get(API_SOURCE_URL, params=params, headers=headers, timeout=30)
            
            if res.status_code == 400:
                if "rest_post_invalid_page_number" in res.text:
                    print(f"Reached end of posts from WordPress API (page {page} does not exist). Stopping fetch.")
                    break
                else:
                    raise Exception(f"Error: Gagal mengambil data dari WordPress REST API: {res.status_code} - {res.text}. "
                                   f"Pastikan URL API Anda benar dan dapat diakses.")
            elif res.status_code != 200:
                raise Exception(f"Error: Gagal mengambil data dari WordPress REST API: {res.status_code} - {res.text}. "
                               f"Pastikan URL API Anda benar dan dapat diakses.")

            posts_batch = res.json()

            if not posts_batch:
                print(f"Fetched empty batch on page {page}. Stopping fetch.")
                break

            for post in posts_batch:
                all_posts_data.append({
                    'id': post.get('id'),
                    'title': post.get('title', {}).get('rendered', ''),
                    'content': post.get('content', {}).get('rendered', ''),
                    'date': post.get('date', '')
                })
            page += 1
            time.sleep(0.5)

        except requests.exceptions.Timeout:
            print(f"Timeout: Permintaan ke WordPress API di halaman {page} habis waktu. Mungkin ada masalah jaringan atau server lambat.")
            break
        except requests.exceptions.RequestException as e:
            print(f"Network Error: Gagal terhubung ke WordPress API di halaman {page}: {e}. Cek koneksi atau URL.")
            break

    return all_posts_data

# === Eksekusi Utama ===
if __name__ == '__main__':
    print(f"[{datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] Starting WordPress to WordPress publishing process...")
    print(f"🚀 Mengambil artikel dari WordPress SUMBER: {API_SOURCE_URL}.")
    print(f"🎯 Akan memposting ke WordPress TARGET: {WP_TARGET_API_URL}.")
    print("🤖 Fitur Pengeditan Judul dan Konten (300 kata pertama) oleh Gemini AI DIAKTIFKAN (menggunakan 2 API Key terpisah).")
    print("🖼️ Mencoba menambahkan gambar acak di awal konten.")

    try:
        # 1. Muat daftar postingan yang sudah diterbitkan
        published_ids = load_published_posts_state()
        print(f"Ditemukan {len(published_ids)} postingan yang sudah diterbitkan sebelumnya.")

        # 2. Muat URL gambar acak
        random_image_urls = load_image_urls(RANDOM_IMAGES_FILE)
        selected_random_image = get_random_image_url(random_image_urls)
        if not selected_random_image:
            print("⚠️ Tidak ada URL gambar acak yang tersedia. Artikel akan diterbitkan tanpa gambar acak di awal.")

        # 3. Ambil semua postingan mentah dari API WordPress SUMBER
        all_posts_raw_data = fetch_raw_posts() # Menggunakan fungsi baru untuk fetch mentah
        print(f"Total {len(all_posts_raw_data)} artikel ditemukan dari WordPress SUMBER.")

        # 4. Filter postingan yang belum diterbitkan
        unpublished_posts = [post for post in all_posts_raw_data if str(post['id']) not in published_ids]
        print(f"Ditemukan {len(unpublished_posts)} artikel dari SUMBER yang belum diterbitkan ke TARGET.")

        if not unpublished_posts:
            print("\n🎉 Tidak ada artikel baru yang tersedia untuk diterbitkan hari ini. Proses selesai.")
            exit()

        # 5. Urutkan postingan yang belum diterbitkan dari yang TERBARU
        # Pastikan format tanggal sudah benar dan dapat dibandingkan
        unpublished_posts.sort(key=lambda x: datetime.datetime.fromisoformat(x['date'].replace('Z', '+00:00')), reverse=True)

        # 6. Pilih satu postingan untuk diterbitkan hari ini (paling baru)
        post_to_publish_data = unpublished_posts[0]

        original_id = post_to_publish_data['id']
        original_title = post_to_publish_data['title']
        original_content = post_to_publish_data['content']

        print(f"🌟 Memproses dan menerbitkan artikel berikutnya: '{original_title}' (ID: {original_id}) dari SUMBER")

        # --- TERAPKAN PENGGANTIAN KATA KHUSUS pada judul & konten SEBELUM pengeditan Gemini ---
        title_after_replacements = replace_custom_words(original_title)
        
        content_no_anchors = remove_anchor_tags(original_content)
        cleaned_formatted_content_before_gemini = strip_html_and_divs(content_no_anchors)
        content_after_replacements = replace_custom_words(cleaned_formatted_content_before_gemini)


        # --- BARU LAKUKAN PENGEDITAN JUDUL DENGAN GEMINI AI UNTUK ARTIKEL YANG DIPILIH SAJA ---
        final_edited_title = edit_title_with_gemini(
            title_after_replacements # Gunakan judul yang sudah diganti kata
        )

        # --- LAKUKAN PENGEDITAN KONTEN (300 kata pertama) DENGAN GEMINI AI UNTUK ARTIKEL YANG DIPILIH SAJA ---
        final_processed_content_text = edit_first_300_words_with_gemini(
            original_id,
            final_edited_title, # Gunakan judul yang sudah diedit Gemini untuk logging
            content_after_replacements # Gunakan konten yang sudah diganti kata
        )
        
        # Konversi double newline menjadi tag paragraf HTML
        final_post_content_html = final_processed_content_text.replace('\n\n', '<p></p>') 

        # 7. Terbitkan ke WordPress TARGET
        published_result = publish_post_to_wordpress(
            WP_TARGET_API_URL,
            final_edited_title, # Menggunakan judul yang sudah diedit Gemini
            final_post_content_html, 
            WP_USERNAME,
            WP_APP_PASSWORD,
            random_image_url=selected_random_image
        )
        
        if published_result:
            # 8. Tambahkan ID postingan (dari SUMBER) ke daftar yang sudah diterbitkan dan simpan state
            published_ids.add(str(original_id))
            save_published_posts_state(published_ids)
            print(f"✅ State file '{STATE_FILE}' diperbarui dengan ID post dari SUMBER: {original_id}.")
            print("\n🎉 Proses Selesai! Artikel telah diterbitkan ke WordPress TARGET.")
        else:
            print("\n❌ Gagal menerbitkan artikel ke WordPress TARGET. State file tidak diperbarui.")

    except Exception as e:
        print(f"❌ Terjadi kesalahan fatal: {e}")
        import traceback
        traceback.print_exc()
