import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io
import logging
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

def compile_pdf():
    # 1. Authenticate
    credentials, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    service = build("drive", "v3", credentials=credentials)
    
    folder_id = "1wrda2hFA3dWE7oS3ilaoS0oamOFGXM_S"
    
    # 2. Get list of page JPEG files
    query = f"'{folder_id}' in parents and mimeType = 'image/jpeg' and trashed = false"
    logger.info("Retrieving list of images in the folder...")
    results = service.files().list(
        q=query,
        pageSize=100,
        fields="files(id, name)"
    ).execute()
    
    files = results.get("files", [])
    # Filter only page_XX.jpg files and sort them numerically
    page_files = []
    for f in files:
        name = f['name']
        if name.startswith("page_") and name.endswith(".jpg"):
            page_files.append(f)
            
    page_files = sorted(page_files, key=lambda x: x['name'])
    
    if not page_files:
        logger.error("No page_XX.jpg files found in the folder!")
        return
        
    logger.info(f"Found {len(page_files)} pages. Downloading and converting...")
    
    pil_images = []
    for f in page_files:
        name = f['name']
        file_id = f['id']
        logger.info(f"Downloading {name}...")
        
        # Download file content
        request = service.files().get_media(fileId=file_id)
        img_buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(img_buffer, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            
        # Convert to PIL Image
        img_buffer.seek(0)
        img = Image.open(img_buffer).convert("RGB")
        pil_images.append(img)
        
    if not pil_images:
        logger.error("No images downloaded successfully.")
        return
        
    # 3. Compile PDF
    logger.info("Compiling pages into a single PDF manuscript...")
    pdf_buffer = io.BytesIO()
    pil_images[0].save(
        pdf_buffer,
        format="PDF",
        save_all=True,
        append_images=pil_images[1:]
    )
    pdf_bytes = pdf_buffer.getvalue()
    
    # 4. Upload PDF back to Drive folder
    pdf_filename = "The Little Helpers That Never Sleep - Print Manuscript.pdf"
    logger.info(f"Uploading compiled PDF '{pdf_filename}'...")
    
    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype="application/pdf", resumable=True)
    file_metadata = {
        'name': pdf_filename,
        'parents': [folder_id]
    }
    
    uploaded_file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    
    logger.info(f"Successfully compiled and uploaded print manuscript PDF! File ID: {uploaded_file.get('id')}")

if __name__ == "__main__":
    compile_pdf()
