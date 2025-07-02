#!/usr/bin/env python3
"""
Bilinguis Web to EPUB Converter
Developed by William N. Johnson
Version: 1.0

Description:
This program is designed to scrape web-based books from bilinguis.com and convert
them into EPUB format. It aims to preserve the side-by-side two-column bilingual 
layout found on bilinguis.com into a reader-friendly table format within the EPUB 
to enhance bilingual reading experience for language learners.

How to run: 
python book_scraper.py [OPTIONS] <bilinguis_webpage_url> <name_of_author> <name_of_work>

Example:
python book_scraper.py "http://bilinguis.com/book/alice/fr/en/" "Lewis Carroll" "Alice's Adventures in Wonderland"

"""

import requests
from bs4 import BeautifulSoup, Tag, NavigableString, Comment # Added Comment
import time
import argparse
import os
import re
from urllib.parse import urljoin, urlparse
from ebooklib import epub
import uuid
import sys
import shutil


class WebToEpubConverter:
    def __init__(self, start_url, author_name, book_title, debug_mode=False):
        self.start_url = start_url
        self.author_name = author_name
        self.book_title = book_title
        self.debug_mode = debug_mode
        self.max_pages = 3 if debug_mode else float('inf')
        self.base_url = self._get_base_url(start_url)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.chapters = [] # Stores data for all processed pages/chapters
        self.epub_nav_chapters = [] # Stores epub.EpubHtml objects that should be in the TOC
        self.css_content = ""
        self.image_files = {}
        self.font_files = {}
        self.temp_resource_dir = 'temp_epub_resources'

        if self.debug_mode:
            print(f"DEBUG: Initialized converter in debug mode (max {self.max_pages} pages)")
            os.makedirs(self.temp_resource_dir, exist_ok=True)


    def _get_base_url(self, url):
        """Extract base URL (scheme://netloc) from the given URL"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"


    def _get_page_content(self, url):
        """Fetch and parse a single page"""
        try:
            print(f"Fetching: {url}")
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'html.parser')
        except requests.exceptions.RequestException as e:
            print(f"Error fetching {url}: {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred while processing {url}: {e}")
            return None


    def _extract_content_slice(self, soup):
        """
        Extract the main content area for a chapter, stopping before the
        third-to-last 'text-center' div.
        """
        # Create a deep copy of the soup to ensure navigation elements are preserved for _find_next_page_url
        # We need this copy so that `_find_next_page_url` can still operate on the full, original structure
        # while `main_content_accumulator` is modified.
        content_soup = BeautifulSoup(str(soup), 'html.parser')

        all_text_center_divs = content_soup.find_all('div', class_='text-center')
        end_content_marker = None
        if len(all_text_center_divs) >= 3:
            end_content_marker = all_text_center_divs[-3]
            if self.debug_mode:
                print(f"DEBUG: Identified end content marker based on third-to-last .text-center div. Its content starts with: '{end_content_marker.get_text(strip=True)[:50]}...'")
        else:
            if self.debug_mode:
                print("DEBUG: Fewer than 3 .text-center divs found, cannot reliably use as end marker. Will attempt to capture all .row divs that look like content.")

        # Create a new tag to accumulate content. This will be the root of our chapter content.
        # It's important this tag is created by a parser object, but in this context, it's fine.
        main_content_accumulator = BeautifulSoup("", 'html.parser').new_tag("div")
        past_content_marker = False

        for row_idx, row in enumerate(all_rows := content_soup.find_all('div', class_='row')): # Use assignment expression for clarity
            if end_content_marker and row == end_content_marker:
                past_content_marker = True
                if self.debug_mode:
                    print(f"DEBUG: Reached the end content marker (row index {row_idx}). Stopping content collection.")
                break
                
            if not past_content_marker:
                if row.find(class_='breadcrumb') or row.find('nav', class_='navbar'):
                    if self.debug_mode:
                        print(f"DEBUG: Skipping row {row_idx} (breadcrumb/navbar/likely non-content header).")
                    continue

                if row.get_text(strip=True) or row.find('img'):
                    main_content_accumulator.append(row)
                    if self.debug_mode:
                        print(f"DEBUG: Appended row {row_idx} to content accumulator.")
                else:
                    if self.debug_mode:
                        print(f"DEBUG: Skipping empty or non-content row {row_idx}.")

        final_content_soup = main_content_accumulator

        # Clean up unwanted tags from the *final_content_soup*
        if final_content_soup:
            for unwanted_tag_name in ['script', 'form', 'iframe', 'nav', 'header', 'footer', 'style', 'noscript']:
                for el in final_content_soup.find_all(unwanted_tag_name):
                    el.decompose()

            unwanted_selectors = [
                {'class_': 'social-share-buttons'},
                {'class_': 'navbar'},
                {'class_': 'breadcrumb'},
                {'class_': 'menu'},
                {'class_': 'prev-next-links'},
                {'id': 'pagination'},
            ]
            for selector in unwanted_selectors:
                for el in final_content_soup.find_all(**selector):
                    if self.debug_mode:
                        print(f"DEBUG: Decomposing element by selector during general cleanup: {selector}")
                    el.decompose()

            # Remove empty tags unless they contain images or links, which might be valid content holders
            for tag in final_content_soup.find_all(['p', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                if not tag.get_text(strip=True) and not tag.find('img') and not tag.find('a', href=True):
                    tag.decompose()

        return final_content_soup


    def _fix_internal_links(self, soup, current_url):
        """Convert URLs to reference parts of the book rather than web URLs"""
        for link in soup.find_all('a', href=True):
            href = link['href']

            if href.startswith('data:') or (href.startswith(('http://', 'https://')) and not href.startswith(self.base_url)):
                continue
            if href.startswith('#'):
                continue

            full_url = urljoin(current_url, href)

            if self.base_url in full_url:
                parsed_full_url = urlparse(full_url)
                clean_full_path = urljoin(self.base_url, parsed_full_url.path)

                found_chapter_idx = -1
                for i, chap_data in enumerate(self.chapters):
                    parsed_chap_url = urlparse(chap_data['url'])
                    clean_chap_path = urljoin(self.base_url, parsed_chap_url.path)

                    if clean_full_path == clean_chap_path:
                        found_chapter_idx = i
                        break

                if found_chapter_idx != -1:
                    link['href'] = f'chap_{found_chapter_idx+1:03d}.xhtml'
                else:
                    # If it's an internal link not mapping to a known chapter, unwrap it.
                    # This avoids broken links in the EPUB.
                    link.unwrap()
                    if self.debug_mode:
                        print(f"DEBUG: Unwrapped potential dead internal link: {full_url}")


    def _find_next_page_url(self, soup, current_url):
        """
        Find the URL of the next page using robust methods, specifically targeting
        bilinguis.com's navigation pattern (often '»' link within a 'text-center' div).
        This function should be called on the *original, unmodified* soup.
        """
        all_links = soup.find_all('a', href=True)

        for link in all_links:
            text = link.get_text().strip()
            if ('»' in text and '«' not in text) or \
               re.search(r'next|suivant|suivante', text, re.IGNORECASE) or \
               (link.get('rel') and 'next' in link.get('rel')):

                next_url = urljoin(current_url, link['href'])
                parsed_current = urlparse(current_url)
                parsed_next = urlparse(next_url)

                if self.base_url in next_url and \
                   (parsed_current.path != parsed_next.path or \
                    parsed_current.query != parsed_next.query or \
                    parsed_current.fragment != parsed_next.fragment):

                    if parsed_next.path == parsed_current.path and parsed_next.fragment:
                        continue # Skip same-page anchors

                    parent_div_text_center = link.find_parent('div', class_='text-center')
                    parent_div_prev_next = link.find_parent('div', class_='prev-next-links')

                    if parent_div_text_center or parent_div_prev_next:
                        if self.debug_mode:
                            print(f"DEBUG: Found strong next link candidate (text/rel/container): {next_url}")
                        return next_url

                    if re.search(r'next|suivant|suivante', text, re.IGNORECASE) and not re.search(r'previous|précédent', text, re.IGNORECASE):
                        if self.debug_mode:
                            print(f"DEBUG: Found next link candidate (clear text): {next_url}")
                        return next_url

        parsed_current_path = urlparse(current_url).path.lower()
        match_current_chapter = re.search(r'/(?:c|chapter|chapitre|part|partie)-?(\d+)', parsed_current_path)

        if match_current_chapter:
            current_chapter_num = int(match_current_chapter.group(1))

            for link in all_links:
                next_url_candidate = urljoin(current_url, link['href'])
                parsed_next_path = urlparse(next_url_candidate).path.lower()

                match_next_chapter = re.search(r'/(?:c|chapter|chapitre|part|partie)-?(\d+)', parsed_next_path)

                if match_next_chapter:
                    candidate_chapter_num = int(match_next_chapter.group(1))
                    if candidate_chapter_num == current_chapter_num + 1:
                        if self.base_url in next_url_candidate:
                            current_prefix = parsed_current_path.split(match_current_chapter.group(0))[0]
                            next_prefix = parsed_next_path.split(match_next_chapter.group(0))[0]
                            if current_prefix == next_prefix: # Ensure it's part of the same sequence
                                if self.debug_mode:
                                    print(f"DEBUG: Found next link by numerical increment: {next_url_candidate}")
                                return next_url_candidate

        return None


    def _download_resource(self, url, folder):
        """Downloads a resource (image, font) and returns its EPUB internal path."""
        full_url = urljoin(self.base_url, url)

        resource_map = self.image_files if folder == 'images' else self.font_files
        if full_url in resource_map:
            return resource_map[full_url]

        try:
            response = self.session.get(full_url, timeout=10)
            response.raise_for_status()

            filename = os.path.basename(urlparse(full_url).path).split('?')[0] # Remove query string
            if not filename or '.' not in filename:
                content_type = response.headers.get('Content-Type', '').lower()
                ext = 'bin'
                if 'image' in content_type:
                    ext = content_type.split('/')[-1].replace('jpeg', 'jpg').replace('svg+xml', 'svg')
                elif 'font' in content_type or 'opentype' in content_type:
                    ext = content_type.split('/')[-1].replace('x-font-', '').replace('sfnt', 'ttf').replace('truetype', 'ttf')
                filename = f"{uuid.uuid4().hex}.{ext}"

            filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename) # Sanitize filename for filesystem and EPUB

            original_filename_base, original_filename_ext = os.path.splitext(filename)
            unique_filename = filename
            counter = 0
            while os.path.exists(os.path.join(self.temp_resource_dir, unique_filename)):
                counter += 1
                unique_filename = f"{original_filename_base}_{counter}{original_filename_ext}"

            temp_path = os.path.join(self.temp_resource_dir, unique_filename)
            os.makedirs(self.temp_resource_dir, exist_ok=True)

            with open(temp_path, 'wb') as f:
                f.write(response.content)

            epub_path = f'{folder}/{unique_filename}' # Path inside the EPUB
            resource_map[full_url] = epub_path

            if self.debug_mode:
                print(f"DEBUG: Downloaded resource: {full_url} to {epub_path}")
            return epub_path

        except requests.exceptions.RequestException as e:
            print(f"Warning: Could not download resource from {full_url}: {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred downloading {full_url}: {e}")
            return None


    def _process_css_urls(self, css_text, base_css_url):
        """Finds and replaces URLs in CSS for images and fonts, downloads them."""

        def replace_url_callback(match):
            original_url_in_css = match.group(2).strip("'\"")

            # Do not process data URIs or external URLs
            if original_url_in_css.startswith('data:') or \
               (original_url_in_css.startswith(('http://', 'https://')) and not original_url_in_css.startswith(self.base_url)):
                return match.group(0)

            full_resource_url = urljoin(base_css_url, original_url_in_css)

            folder = None
            if re.search(r'\.(png|jpg|jpeg|gif|svg|webp)(\?.*)?$', original_url_in_css, re.IGNORECASE):
                folder = 'images'
            elif re.search(r'\.(ttf|otf|woff|woff2|eot)(\?.*)?$', original_url_in_css, re.IGNORECASE):
                folder = 'fonts'
            else:
                return match.group(0) # Not an image or font, return as is

            epub_resource_path = self._download_resource(full_resource_url, folder)

            if epub_resource_path:
                # Relative path from CSS file (style/default.css) to resources (images/, fonts/)
                relative_path_for_css = os.path.join('..', epub_resource_path).replace('\\', '/')
                return f"{match.group(1)}{relative_path_for_css}{match.group(3)}"

            return match.group(0) # If download failed, keep original URL (might break display)

        # Regex to find url() in CSS
        # Added the `url()` part to be more robust, and handling of various quote types.
        processed_css = re.sub(r'(url\(\s*[\'"]?)(.*?)([\'"]?\s*\))', replace_url_callback, css_text, flags=re.IGNORECASE)
        return processed_css


    def _wrap_content_in_paragraph(self, source_tag_or_string, target_tag, main_parser_soup):
        """
        Helper to take content from a source_tag or string and append it to target_tag,
        ensuring raw text nodes and simple inline tags are wrapped in a <p>.
        Handles lists (ul/ol) by appending them directly.
        """
        # Ensure source_tag_or_string is a Tag for consistent processing
        if isinstance(source_tag_or_string, NavigableString):
            # If it's just a string, wrap it in a temporary div to process its contents
            temp_div = main_parser_soup.new_tag("div")
            temp_div.append(source_tag_or_string)
            source_tag_or_string = temp_div
        elif not isinstance(source_tag_or_string, Tag):
            # Should not happen, but for safety
            if self.debug_mode:
                print(f"WARNING: _wrap_content_in_paragraph received unexpected type: {type(source_tag_or_string)}")
            return

        current_p = None
        for child in list(source_tag_or_string.contents): # Iterate over a copy to allow modification
            # Skip empty strings or comments
            if isinstance(child, NavigableString) and not child.strip():
                continue
            if isinstance(child, Comment): # Use imported Comment
                continue

            # If it's a block-level tag (like another <p>, <div>, <h1> etc.),
            # or an image/link that should stand alone, append it directly.
            # Close any open paragraph before appending a block.
            if isinstance(child, Tag):
                if child.name in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'blockquote', 'pre']:
                    if current_p:
                        target_tag.append(current_p)
                        current_p = None
                    # Recursively wrap content within nested block tags as well, especially div
                    if child.name == 'div': # Treat nested divs like containers
                        new_div_content = main_parser_soup.new_tag("div")
                        new_div_content.attrs = child.attrs # Keep original attributes
                        self._wrap_content_in_paragraph(child, new_div_content, main_parser_soup)
                        target_tag.append(new_div_content)
                    else: # For other block tags, just append
                        target_tag.append(child)
                elif child.name == 'img' or (child.name == 'a' and child.has_attr('href') and child.find('img')):
                    # Special handling for images, ensure they are block-level if they aren't already
                    if current_p:
                        target_tag.append(current_p)
                        current_p = None
                    target_tag.append(child)
                else: # Must be an inline tag (span, strong, em, a without img, etc.)
                    if not current_p:
                        current_p = main_parser_soup.new_tag("p")
                    current_p.append(child)
            else: # Must be NavigableString (raw text)
                if not current_p:
                    current_p = main_parser_soup.new_tag("p")
                current_p.append(child)

        # After the loop, append any remaining open paragraph
        if current_p and current_p.get_text(strip=True): # Only append if paragraph has content
            target_tag.append(current_p)


    def _restructure_bilingual_content(self, content_soup_fragment, main_parser_soup):
        """
        Transforms consecutive Bootstrap-like row/col-xs-6 divs within the content_soup_fragment
        into a single HTML table for robust two-column bilingual layout in EPUB.
        Preserves non-bilingual content.
        """
        # Create a new BeautifulSoup object to build the structured output
        structured_content_accumulator = main_parser_soup.new_tag("div")

        # Iterate through the direct children of the content_soup_fragment
        elements_to_process = list(content_soup_fragment.contents)
        
        current_group_of_bilingual_rows = []

        for element in elements_to_process:
            is_bilingual_row_tag = False
            if isinstance(element, Tag) and element.name == 'div' and 'row' in element.get('class', []):
                # Using recursive=False is crucial here to only find direct children cols
                cols = element.find_all('div', class_='col-xs-6', recursive=False) 
                if len(cols) == 2:
                    # Ensure both columns have some content before considering it a bilingual row
                    if (cols[0].get_text(strip=True) or cols[0].find('img')) and \
                       (cols[1].get_text(strip=True) or cols[1].find('img')):
                        is_bilingual_row_tag = True
                    elif self.debug_mode:
                        print(f"DEBUG: Skipping col-xs-6 row due to empty column(s): {element.get_text(strip=True)[:100]}")


            if is_bilingual_row_tag:
                current_group_of_bilingual_rows.append(element)
            else:
                # If current element is NOT a bilingual row,
                # first, process any pending bilingual group
                if current_group_of_bilingual_rows:
                    new_table = main_parser_soup.new_tag("table", class_="epub-bilingual-table")
                    for row_div in current_group_of_bilingual_rows:
                        cols = row_div.find_all('div', class_='col-xs-6', recursive=False)
                        if len(cols) != 2: # Should not happen if logic is correct, but defensive check
                            if self.debug_mode:
                                print(f"WARNING: Grouped row_div did not contain 2 cols: {row_div.get_text(strip=True)[:100]}")
                            continue # Skip malformed row

                        new_tr = main_parser_soup.new_tag("tr")

                        # Process Left Column (Language 1)
                        td1 = main_parser_soup.new_tag("td")
                        if cols[0].has_attr('lang'): td1['lang'] = cols[0]['lang']
                        self._wrap_content_in_paragraph(cols[0], td1, main_parser_soup)
                        new_tr.append(td1)

                        # Process Right Column (Language 2)
                        td2 = main_parser_soup.new_tag("td")
                        if cols[1].has_attr('lang'): td2['lang'] = cols[1]['lang']
                        self._wrap_content_in_paragraph(cols[1], td2, main_parser_soup)
                        new_tr.append(td2)

                        new_table.append(new_tr)
                    
                    if new_table.contents: # Only append if table has rows
                        structured_content_accumulator.append(new_table)
                        if self.debug_mode:
                            print(f"DEBUG: Appended a table for {len(current_group_of_bilingual_rows)} bilingual rows.")
                    current_group_of_bilingual_rows = [] # Reset group

                # Append the non-bilingual element
                structured_content_accumulator.append(element)
                if self.debug_mode and isinstance(element, Tag):
                    print(f"DEBUG: Appended non-bilingual element: <{element.name}>")
                elif self.debug_mode and isinstance(element, NavigableString) and element.strip():
                     print(f"DEBUG: Appended non-bilingual text: '{element.strip()[:50]}...'")

        # Process any remaining bilingual group after the loop
        if current_group_of_bilingual_rows:
            new_table = main_parser_soup.new_tag("table", class_="epub-bilingual-table")
            for row_div in current_group_of_bilingual_rows:
                cols = row_div.find_all('div', class_='col-xs-6', recursive=False)
                if len(cols) != 2: # Defensive check
                    if self.debug_mode:
                        print(f"WARNING: Final grouped row_div did not contain 2 cols: {row_div.get_text(strip=True)[:100]}")
                    continue
                new_tr = main_parser_soup.new_tag("tr")

                td1 = main_parser_soup.new_tag("td")
                if cols[0].has_attr('lang'): td1['lang'] = cols[0]['lang']
                self._wrap_content_in_paragraph(cols[0], td1, main_parser_soup)
                new_tr.append(td1)

                td2 = main_parser_soup.new_tag("td")
                if cols[1].has_attr('lang'): td2['lang'] = cols[1]['lang']
                self._wrap_content_in_paragraph(cols[1], td2, main_parser_soup)
                new_tr.append(td2)

                new_table.append(new_tr)
            
            if new_table.contents: # Only append if table has rows
                structured_content_accumulator.append(new_table)
                if self.debug_mode:
                    print(f"DEBUG: Appended final table for {len(current_group_of_bilingual_rows)} bilingual rows.")

        return structured_content_accumulator


    def _extract_and_inline_css(self, soup):
        """
        Extracts CSS from <style> tags and <link rel="stylesheet">,
        and includes critical CSS for font (Arial) and general layout.
        Includes specific CSS for the new table-based bilingual layout.
        """
        css_content = """
/* BASE STYLES & RESETS: Be extremely aggressive to fight user-agent stylesheets */
html, body, p, div, span, h1, h2, h3, h4, h5, h6, li, a, strong, em, blockquote, pre {
    font-family: Arial, Helvetica, sans-serif !important;
    line-height: 1.6 !important; /* Consistent line height */
    word-wrap: break-word !important; /* Ensure long words break */
    font-size: 0.9em !important; /* Make font size somewhat smaller */
    margin: 0 !important; /* Reset all margins */
    padding: 0 !important; /* Reset all padding */
    border: 0 !important; /* Reset all borders */
    box-sizing: border-box !important; /* Critical for layout calculations */
    text-indent: 0 !important; /* Remove any default text indentation */
}

/* Specific margins for paragraphs to maintain readability */
p {
    margin-bottom: 1em !important; /* Add space between paragraphs */
}

/* Heading margins */
h1, h2, h3, h4, h5, h6 {
    margin-top: 1em !important;
    margin-bottom: 0.5em !important;
    line-height: 1.2 !important;
}

/* Ensure images fit within their parent containers (e.g., the columns) */
img {
    max-width: 100% !important; /* Force max-width for images */
    height: auto !important;
    display: block !important; /* Removes extra space below images */
    margin: 0 auto !important; /* Center images */
    padding: 5px 0 !important; /* Some padding around images */
}

/* Basic text alignments if needed, common classes from original site */
.text-center { text-align: center !important; }
.text-left { text-align: left !important; }
.text-right { text-align: right !important; }

/* Styles for the new table-based bilingual layout */
.epub-bilingual-table {
    width: 100% !important;
    border-collapse: collapse !important; /* Ensure no gaps between cells */
    border-spacing: 0 !important; /* Explicitly remove spacing between cells */
    margin: 1em 0 !important; /* Add some margin around the table */
    table-layout: fixed !important; /* FORCES equal column widths regardless of content */
    /* background-color: #f8f8f8; /* For debugging: see the table's overall area */ */
}

.epub-bilingual-table tr {
    height: auto !important; /* Ensure rows can expand naturally */
    vertical-align: top !important; /* Ensure row content aligns to top (redundant but safe) */
}

.epub-bilingual-table td {
    width: 50% !important; /* Each column takes 50% width */
    padding: 0.5em !important; /* Symmetrical padding inside cells */
    border: none !important; /* Ensure no inherent borders on cells */
    vertical-align: top !important; /* Align content to the top of the cell */
    overflow-wrap: break-word !important; /* Ensure long words break within the column */
    word-break: break-word !important; /* For older browsers/e-readers, too */
    box-sizing: border-box !important; /* CRITICAL: Ensure padding is included in the 50% width */
    line-height: 1.6em !important; /* Enforce consistent line height */
    text-align: left !important; /* Ensure text is left-aligned within its cell */
    /* Remove any implicit 'display' rules that might break flow within TD */
    display: table-cell !important; /* Explicitly ensure it's a table cell */

    /* For a clear vertical divide: */
    border-right: 1px solid #ccc !important; /* Light gray border on the right of the left cell */
}

/* Specific styling for the right-hand column to remove its right border */
.epub-bilingual-table td:last-child {
    border-right: none !important;
}

/* Ensure text alignment and resets within table cells' children.
   These elements should mostly rely on parent td's text-align and line-height.
   Do NOT force `display: block` on elements that should be inline like span, strong, em, a.
*/
.epub-bilingual-table td p,
.epub-bilingual-table td div,
.epub-bilingual-table td h1,
.epub-bilingual-table td h2,
.epub-bilingual-table td h3,
.epub-bilingual-table td h4,
.epub-bilingual-table td h5,
.epub-bilingual-table td h6,
.epub-bilingual-table td ul,
.epub-bilingual-table td ol,
.epub-bilingual-table td li,
.epub-bilingual-table td blockquote,
.epub-bilingual-table td pre {
    margin: 0 !important;
    padding: 0 !important;
    font-size: 1em !important;
    line-height: inherit !important;
    text-align: inherit !important;
    border: 0 !important; /* Aggressive reset */
}

/* For inline-level elements within TD, only reset basic presentation */
.epub-bilingual-table td span,
.epub-bilingual-table td strong,
.epub-bilingual-table td em,
.epub-bilingual-table td a {
    margin: 0 !important;
    padding: 0 !important;
    font-size: 1em !important;
    line-height: inherit !important;
    text-align: inherit !important;
    /* Explicitly keep them as inline or inline-block if needed for special cases */
    display: inline !important; /* Ensure they remain inline, not block */
    white-space: normal !important; /* Allow normal wrapping */
}

/* Reduce spacing from language attributes if they are present as spans around text */
/* This rule is fine if these [lang] tags are meant to be block-level wrappers for paragraphs */
[lang] {
    /* If [lang] is used on a P, DIV, Hx, it's fine. If it's on a SPAN within a P, don't force block. */
    /* Generally, if [lang] is on the TD itself, or a P/DIV within TD, this rule is less critical here. */
    /* If content is wrapped in <p lang="fr">, then p styling applies. */
    /* If it's <div><span lang="fr">text</span></div>, then span is inline. */
    /* Let's try to make it work where [lang] might be a direct child of td or p */
    /* display: block !important; */ /* Re-enable ONLY if needed for specific [lang] elements */
    margin: 0 !important;
    padding: 0 !important;
    line-height: inherit !important;
    white-space: normal !important;
}

/* Ensure no floats interfere */
.row::after, .clearfix::after {
    content: "" !important;
    display: table !important;
    clear: both !important;
}
.col-xs-6 { /* Aggressively reset float for original col-xs-6, though they are decomposed */
    float: none !important;
    width: auto !important; /* Reset original column width if they somehow persist */
}

/* Aggressively reset all lists */
ul, ol {
    list-style: none !important;
    margin: 0 !important;
    padding: 0 !important;
}
li {
    margin: 0 !important;
    padding: 0 !important;
}

/* Prevent extra spacing from line breaks */
br {
    line-height: 0 !important; /* Should not be necessary but as last resort */
}

"""
        # Process <style> tags
        for style_tag in soup.find_all('style'):
            style_text = style_tag.get_text()
            css_content += self._process_css_urls(style_text, self.base_url) + "\n"

        # Process <link rel="stylesheet"> tags
        for link_tag in soup.find_all('link', rel='stylesheet'):
            href = link_tag.get('href')
            if href:
                try:
                    css_url = urljoin(self.base_url, href)
                    css_response = self.session.get(css_url, timeout=10)
                    css_response.raise_for_status()
                    css_text = css_response.text
                    css_content += self._process_css_urls(css_text, css_url) + "\n"
                except requests.exceptions.RequestException as e:
                    print(f"Warning: Could not fetch CSS from {href}: {e}")
                except Exception as e:
                    print(f"An unexpected error occurred processing CSS from {href}: {e}")

        self.css_content = css_content # Update self.css_content here
        return css_content


    def _process_page_content(self, current_url, page_count, soup):
        """Processes content for a single page."""
        # Extract CSS only from the first page
        if page_count == 1:
            self.css_content = self._extract_and_inline_css(soup) # This updates self.css_content directly
            if self.debug_mode:
                print(f"DEBUG: Extracted {len(self.css_content)} characters of CSS.")

        next_url = self._find_next_page_url(soup, current_url)

        # Extract content slice. This returns a *new* BeautifulSoup object containing the slice.
        content_soup_fragment = self._extract_content_slice(soup)

        if not content_soup_fragment or not content_soup_fragment.get_text(strip=True):
            print(f"Warning: No meaningful content extracted for {current_url}.")
            return None, next_url

        if self.debug_mode:
            print(f"DEBUG: Extracted content (HTML fragment) length: {len(str(content_soup_fragment))}")

        # Restructure bilingual content from divs to tables.
        # This function returns a *new* structured soup fragment.
        content_soup_fragment = self._restructure_bilingual_content(content_soup_fragment, soup)

        # Process images within the extracted content (now potentially inside tables)
        for img in content_soup_fragment.find_all('img', src=True):
            original_img_src = img['src']
            if original_img_src.startswith('data:'):
                if self.debug_mode:
                    print("DEBUG: Skipping data URI image.")
                continue

            epub_img_path = self._download_resource(original_img_src, 'images')
            if epub_img_path:
                img['src'] = epub_img_path
                if self.debug_mode:
                    print(f"DEBUG: Rewrote img src {original_img_src} to {img['src']}")
            else:
                # Remove image tag if download fails to prevent broken image icons
                if self.debug_mode:
                    print(f"DEBUG: Removing image {original_img_src} due to download failure.")
                img.decompose()

        self._fix_internal_links(content_soup_fragment, current_url)

        chapter_title = self._get_chapter_title(content_soup_fragment, current_url, page_count)

        chapter_data = {
            'title': chapter_title,
            'content': str(content_soup_fragment),
            'url': current_url
        }
        self.chapters.append(chapter_data)

        # Determine if this chapter should be a bookmark in the EPUB's TOC
        is_chapter_url = re.search(r'/(?:c|chapter|part)-?(\d+)', urlparse(current_url).path.lower())
        is_chapter_title = re.search(r'^(chapter|part)\s*\d+', chapter_title.strip(), re.IGNORECASE)

        if is_chapter_url and is_chapter_title:
            if self.debug_mode:
                print(f"DEBUG: Page identified as a new logical chapter for TOC: '{chapter_title}' from URL '{current_url}'")
            # This is where we create the EpubHtml object for the TOC entry
            # We don't add it to book.add_item() yet, only store it for self.epub_nav_chapters
            epub_chapter_item = epub.EpubHtml(
                uid=f"chap_{uuid.uuid4().hex}", # ADD THIS LINE: Assign a unique ID
                title=chapter_data['title'],
                file_name=f'chap_{len(self.chapters):03d}.xhtml', # Use length of all chapters for filename
                lang='en' # Assuming English, can be dynamic if detected
            )
            # We only need the chapter title and file_name for the TOC entry
            self.epub_nav_chapters.append(epub_chapter_item)
        else:
            if self.debug_mode:
                print(f"DEBUG: Page NOT identified as a new logical chapter for TOC: '{chapter_title}' from URL '{current_url}'")

        return chapter_data, next_url


    def _get_chapter_title(self, content_soup_tag, current_url, page_count):
        """Determines the chapter title from content or URL."""
        # Try to find a prominent title within the content first
        title_tag = content_soup_tag.find(['h1', 'h2', 'h3'], class_=['text-center', 'chapter-title', 'section-title'])
        if title_tag and title_tag.get_text(strip=True):
            extracted_title = title_tag.get_text(strip=True)
            if self.debug_mode:
                print(f"DEBUG: Extracted chapter title from H1/H2/H3 tag: '{extracted_title}'")
            return extracted_title

        # Fallback to URL-based chapter detection
        parsed_url_path = urlparse(current_url).path.lower()
        match = re.search(r'/(?:c|chapter|chapitre|part|partie)-?(\d+)', parsed_url_path)
        if match:
            chapter_num = int(match.group(1))
            if self.debug_mode:
                print(f"DEBUG: Extracted chapter title from URL (pattern match): 'Chapter {chapter_num}'")
            return f"Chapter {chapter_num}"
        elif "introduction" in parsed_url_path:
            return "Introduction"
        elif "preface" in parsed_url_path:
            return "Preface"
        
        # If no clear chapter title from content or URL, use a generic fallback.
        # This page will still be added to the book, but won't be in the TOC unless explicitly added.
        return f"Page {page_count}" # Fallback for pages that don't fit chapter pattern


    def scrape_book(self):
        """Main scraping method to fetch pages and extract content."""
        current_url = self.start_url
        page_count = 0
        processed_urls = set()

        print(f"Starting to scrape book: {self.book_title}")
        print(f"Author: {self.author_name}")
        print(f"Starting URL: {current_url}")

        while current_url and page_count < self.max_pages:
            if current_url in processed_urls:
                if self.debug_mode:
                    print(f"DEBUG: URL '{current_url}' already processed. Stopping to prevent loop.")
                break
            processed_urls.add(current_url)

            page_count += 1
            print(f"\nProcessing page {page_count}: {current_url}")

            soup = self._get_page_content(current_url)
            if not soup:
                print("Failed to fetch page, cannot determine next URL from current context. Stopping.")
                break

            # The _process_page_content function now handles extracting content and then restructuring it.
            # It also passes the main `soup` object to the restructuring function for new tag creation.
            chapter_data, next_url = self._process_page_content(current_url, page_count, soup)

            if chapter_data is None: # Content extraction failed, check if next_url exists to continue
                if next_url:
                    print("Skipping current page due to content extraction error, attempting next...")
                    current_url = next_url
                    time.sleep(3)
                    continue
                else:
                    print("No content extracted and no next URL found. Stopping.")
                    break

            if self.debug_mode:
                print(f"DEBUG: Next URL determined: {next_url}")

            if next_url and next_url != current_url and page_count < self.max_pages:
                current_url = next_url
                print("Waiting 3 seconds before next page...")
                time.sleep(3)
            else:
                if page_count >= self.max_pages:
                    print(f"DEBUG: Reached maximum pages limit ({self.max_pages}). Stopping.")
                else:
                    print("No more unique pages found. Stopping scraping.")
                break

        print(f"\nScraping completed. Found {len(self.chapters)} pages/segments.")
        print(f"Identified {len(self.epub_nav_chapters)} chapters for EPUB Table of Contents.")
        return len(self.chapters) > 0


    def _add_epub_resources(self, book):
        """Adds CSS, images, and fonts to the EPUB book."""
        if self.css_content:
            css_item = epub.EpubItem(
                uid="style_default",
                file_name="style/default.css",
                media_type="text/css",
                content=self.css_content.encode('utf-8')
            )
            book.add_item(css_item)
            if self.debug_mode:
                print(f"DEBUG: Added default CSS to EPUB ({len(self.css_content)} chars).")

        for original_url, epub_path in self.image_files.items():
            try:
                temp_file_name = os.path.basename(epub_path)
                temp_path_full = os.path.join(self.temp_resource_dir, temp_file_name)
                with open(temp_path_full, 'rb') as f:
                    img_data = f.read()

                media_type = 'application/octet-stream' # Default
                # Determine proper media type based on extension
                if epub_path.endswith('.png'): media_type = 'image/png'
                elif epub_path.endswith('.jpg') or epub_path.endswith('.jpeg'): media_type = 'image/jpeg'
                elif epub_path.endswith('.gif'): media_type = 'image/gif'
                elif epub_path.endswith('.svg'): media_type = 'image/svg+xml'
                elif epub_path.endswith('.webp'): media_type = 'image/webp' # WebP might not be supported by all readers

                img_item = epub.EpubItem(uid=f"img_{uuid.uuid4().hex}", file_name=epub_path,
                                         media_type=media_type, content=img_data)
                book.add_item(img_item)
                if self.debug_mode:
                    print(f"DEBUG: Added image: {epub_path}")
            except Exception as e:
                print(f"Error adding image {epub_path} to EPUB: {e}")

        for original_url, epub_path in self.font_files.items():
            try:
                temp_file_name = os.path.basename(epub_path)
                temp_path_full = os.path.join(self.temp_resource_dir, temp_file_name)
                with open(temp_path_full, 'rb') as f:
                    font_data = f.read()

                media_type = 'application/octet-stream' # Default
                # Determine proper media type for fonts
                if epub_path.endswith('.ttf'): media_type = 'application/font-sfnt' # TrueType
                elif epub_path.endswith('.otf'): media_type = 'application/font-sfnt' # OpenType
                elif epub_path.endswith('.woff'): media_type = 'application/font-woff'
                elif epub_path.endswith('.woff2'): media_type = 'font/woff2' # EPUB3, not all readers
                elif epub_path.endswith('.eot'): media_type = 'application/vnd.ms-fontobject' # IE-specific

                font_item = epub.EpubItem(uid=f"font_{uuid.uuid4().hex}", file_name=epub_path,
                                         media_type=media_type, content=font_data)
                book.add_item(font_item)
                if self.debug_mode:
                    print(f"DEBUG: Added font: {epub_path}")
            except Exception as e:
                print(f"Error adding font {epub_path} to EPUB: {e}")


    def _add_epub_chapters(self, book):
        """Creates and adds HTML chapters to the EPUB book."""
        all_epub_content_items = [] # All HTML pages, regardless of whether they are in TOC

        for i, chapter_data in enumerate(self.chapters):
            chapter = epub.EpubHtml(
                title=chapter_data['title'],
                file_name=f'chap_{i+1:03d}.xhtml', # Filename based on sequential processing order
                lang='en' # Assuming English, can be dynamic if detected
            )

            content_html = chapter_data['content']
            # Clean up any remaining XML/DOCTYPE declarations if BeautifulSoup adds them
            content_html = re.sub(r'<\?xml[^>]*\?>', '', content_html)
            content_html = re.sub(r'<!DOCTYPE[^>]*>', '', content_html)

            # Ensure the content is wrapped in a block-level element if it's just raw text
            if not content_html.strip().startswith('<'):
                content_html = f'<div>{content_html}</div>'


            html_content = f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>{chapter_data['title']}</title>
    <meta charset="utf-8"/>
    <link rel="stylesheet" type="text/css" href="../style/default.css"/>
</head>
<body>
    {content_html}
</body>
</html>'''

            chapter.content = html_content.encode('utf-8')
            if self.debug_mode:
                print(f"DEBUG: Chapter {i+1} (file: {chapter.file_name}) HTML content length: {len(html_content)}")

            book.add_item(chapter)
            all_epub_content_items.append(chapter)

            # Update the file_name in self.epub_nav_chapters for accurate TOC linking
            # This is important because the file_name is decided here based on `i+1`
            for nav_chap in self.epub_nav_chapters:
                # If the URL matches, update the file_name. Note: this relies on URL being unique to chap file.
                if nav_chap.title == chapter_data['title'] and nav_chap.file_name == f'chap_{len(self.chapters):03d}.xhtml': # This check uses length of chapters at time of chapter creation, not current `i`.
                    # This logic needs to be more robust. Let's fix this slightly.
                    # The `epub_nav_chapters` stores the EpubHtml objects *before* we definitively know the final filename.
                    # A more robust way is to pass the actual EpubHtml object from here to epub_nav_chapters.

                    # Correction: self.epub_nav_chapters already stores the EpubHtml object created in _process_page_content
                    # and that object already has the correct file_name (f'chap_{len(self.chapters):03d}.xhtml' from that moment).
                    # So, no update needed here. The original `file_name` assigned there is based on `len(self.chapters)`
                    # at the time of its creation, which corresponds to `i+1` here.
                    pass # The logic works if file_name is correctly set when added to epub_nav_chapters

        return all_epub_content_items


    def create_epub(self, output_filename=None):
        """Creates the EPUB file from scraped content and resources."""
        if not self.chapters:
            print("No chapters to convert. Please scrape content first.")
            return False

        if not output_filename:
            safe_title = re.sub(r'[^a-zA-Z0-9\s]', '', self.book_title)
            output_filename = f"{safe_title.replace(' ', '_')}.epub"

        print(f"\nCreating EPUB: {output_filename}")

        book = epub.EpubBook()
        book.set_identifier(str(uuid.uuid4()))
        book.set_title(self.book_title)
        book.set_language('en') # Default language for the book metadata
        book.add_author(self.author_name)

        self._add_epub_resources(book)
        all_epub_content_items = self._add_epub_chapters(book) # All xhtml pages

        # Set the table of contents using only the chapters identified for navigation
        book.toc = self.epub_nav_chapters # Use the filtered list of EpubHtml objects

        book.add_item(epub.EpubNcx()) # Navigation control file (required by EPUB 2)
        # book.add_item(epub.EpubNav()) # REMOVED: This creates the *physical* HTML TOC page.

        # Define reading order: All content items in sequence
        book.spine = all_epub_content_items
        
        # If no explicit navigation page, ensure the first chapter is usually readable first
        # This will depend on the EPUB reader, but setting spine correctly is key.

        try:
            epub.write_epub(output_filename, book, {})
            print(f"EPUB created successfully: {output_filename}")
            print(f"File size: {os.path.getsize(output_filename) / 1024:.1f} KB")
            return True
        except Exception as e:
            print(f"Error creating EPUB: {e}")
            if self.debug_mode:
                import traceback
                traceback.print_exc()
            return False
        finally:
            # Clean up temporary resources unless in debug mode
            if not self.debug_mode and os.path.exists(self.temp_resource_dir):
                try:
                    shutil.rmtree(self.temp_resource_dir)
                    if self.debug_mode:
                        print(f"DEBUG: Cleaned up temporary resource directory: {self.temp_resource_dir}")
                except OSError as e:
                    print(f"Error removing temporary directory {self.temp_resource_dir}: {e}")


def main():
    parser = argparse.ArgumentParser(description='Convert web book to EPUB format')
    parser.add_argument('url', help='Starting URL of the web book (e.g., "https://www.bilinguis.com/some-book-chapter-1")')
    parser.add_argument('author', help='Author name (e.g., "Lewis Carroll")')
    parser.add_argument('title', help='Book title (e.g., "Alice in Wonderland Bilingual")')
    parser.add_argument('-o', '--output', help='Output EPUB filename (e.g., "Alice_Bilingual.epub")')
    parser.add_argument('-d', '--debug', action='store_true',
                            help='Debug mode: process only first 3 pages with verbose output and keep temp files (for inspection).')

    args = parser.parse_args()

    # Runtime check for required libraries
    try:
        import ebooklib
        import requests
        from bs4 import BeautifulSoup
    except ImportError as e:
        print(f"Error: Missing required library: {e.name}")
        print(f"Install it with: pip install {e.name.replace('bs4', 'beautifulsoup4')}")
        sys.exit(1)

    converter = WebToEpubConverter(args.url, args.author, args.title, debug_mode=args.debug)

    if converter.scrape_book():
        if converter.create_epub(args.output):
            print("\nEPUB creation process finished.")
            if args.debug:
                print("DEBUG: EPUB created successfully in debug mode!")
                print(f"Temporary resources kept in '{converter.temp_resource_dir}'. Remember to test opening the EPUB file with an e-reader or EPUB validator.")
        else:
            print("Failed to create EPUB file.")
            sys.exit(1)
    else:
        print("Failed to scrape book content. No chapters found or critical error occurred.")
        sys.exit(1)


if __name__ == "__main__":
    main()
