# Bilinguis Web to EPUB Converter (Version 1.0) by William N. Johnson

This program scrapes web-based books from [bilinguis.com](https://www.google.com/search?q=https://www.bilinguis.com) and converts them into EPUB format. It preserves the side-by-side two-column bilingual layout by restructuring it into a table format within the EPUB, enhancing the reading experience for language learners.

## Features

* Scrapes multi-page web books from Bilinguis.

* Downloads and embeds images and fonts.

* Restructures bilingual content into EPUB-friendly HTML tables, with side-by-side viewing.

## Installation

1. **Prerequisites:** Ensure you have Python 3 installed.

2. **Install Dependencies:**
   This script requires the `requests` and `beautifulsoup4` (bs4) libraries, and `ebooklib`.
   You can install them using pip:
   > pip install requests beautifulsoup4 ebooklib

## Usage

### How to Run:
> python book_scraper.py [OPTIONS]

### Example:
> python book_scraper.py "http://bilinguis.com/book/alice/fr/en/" "Lewis Carroll" "Alice's Adventures in Wonderland"

### Arguments

* `<bilinguis_webpage_url>`: The URL of the first page/chapter of the web book (e.g., `"https://www.bilinguis.com/book/alice/fr/en/"`).

* `<name_of_author>`: The author's name (e.g., `"Lewis Carroll"`). Enclose in quotes if it contains spaces.

* `<name_of_work>`: The title of the book (e.g., `"Alice in Wonderland Bilingual"`). Enclose in quotes if it contains spaces.

### Options

* `-d`, `--debug`: Optional. Enable debug mode. This will:

  * Limit scraping to the first 3 pages.

  * Print verbose debug messages to the console.

  * Keep temporary resource files for inspection (located in a `temp_epub_resources` directory).

## Important Notes

* **Website Structure:** This scraper is specifically tailored for the HTML structure of `bilinguis.com`. Its effectiveness on other websites is not guaranteed.

* **Content Extraction:** The script attempts to intelligently extract the main content area and filter out navigation, headers, and footers. If content is missing, you may need to adjust the `_extract_content_slice` method.

* **Error Handling:** The script includes basic error handling for network issues and resource downloads. Debug mode provides more verbose output for troubleshooting.

* **Temporary Files:** In debug mode, a `temp_epub_resources` directory will be created to store downloaded images and fonts. This directory is automatically cleaned up in normal (non-debug) mode upon successful EPUB creation.
