from concurrent.futures import ThreadPoolExecutor
import logging
import os
import pytesseract
from app.core.config import settings
from pdf2image import convert_from_path
import PyPDF2 


pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
logger = logging.getLogger(__name__)

# Helper functions
def extract_text_from_image(image):
    """Extract text from an image using OCR"""
    return pytesseract.image_to_string(image)


# Define process_page as a standalone function to avoid pickling issues
def process_page(args):
    """Process a single PDF page"""
    pdf_path, page_num = args
    images = convert_from_path(
        pdf_path, 
        first_page=page_num + 1, 
        last_page=page_num + 1
    )
    if images:
        return extract_text_from_image(images[0])
    return ""


def process_pdf(pdf_path):
    """Convert PDF to images and extract text using thread pool for faster processing"""
    # First just get page count without loading all pages
    with open(pdf_path, 'rb') as pdf_file:
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        page_count = len(pdf_reader.pages)
    
    # Check if this is a small document - if so, process normally
    if page_count <= 5:
        images = convert_from_path(pdf_path)
        text = ""
        for image in images:
            text += extract_text_from_image(image) + "\n\n"
        return text, page_count
    
    # For larger documents, use thread pool instead of multiprocessing
    # to avoid pickling issues
    max_workers = min(os.cpu_count() or 4, 8)  # Limit to 8 threads max
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Create a list of arguments for each page
        args_list = [(pdf_path, page_num) for page_num in range(page_count)]
        # Process pages in parallel
        page_texts = list(executor.map(process_page, args_list))
    
    text = "\n\n".join(page_texts)
    return text, page_count


# Function to check page count for PDF files
def get_pdf_page_count(file_path):
    """Get page count from PDF file"""
    try:
        with open(file_path, 'rb') as pdf_file:
            pdf_reader = PyPDF2.PdfReader(pdf_file)
            return len(pdf_reader.pages)
    except Exception as e:
        logging.exception("Failed to read PDF page count")
        raise ValueError("Could not read page count: Invalid PDF file")