import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime
import pytz
from feedgen.feed import FeedGenerator
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def get_project_root():
    """Get the project root directory."""
    return Path(__file__).parent.parent


def ensure_feeds_directory():
    """Ensure the feeds directory exists."""
    feeds_dir = get_project_root() / "feeds"
    feeds_dir.mkdir(exist_ok=True)
    return feeds_dir


def fetch_blog_content(url="https://claude.com/blog"):
    """Fetch blog content from Claude's blog."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"Error fetching blog content: {str(e)}")
        raise


def extract_title(card):
    """Extract title using multiple fallback selectors."""
    # Try to find text directly in the link
    text = card.get_text(strip=True)
    if text and len(text) > 10 and not text.startswith('http'):
        return text
    return None


def extract_date_from_article_page(article_url):
    """Fetch article page and extract publication date."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # Ensure full URL
        if article_url.startswith('/'):
            article_url = f"https://claude.com{article_url}"

        response = requests.get(article_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Try multiple selectors for date
        date_selectors = [
            'time[datetime]',
            'meta[property="article:published_time"]',
            'meta[name="publication_date"]',
            'span[class*="date"]',
            'p[class*="date"]',
        ]

        for selector in date_selectors:
            elem = soup.select_one(selector)
            if elem:
                # Try to get datetime attribute
                date_str = elem.get('datetime') or elem.get('content') or elem.get_text(strip=True)
                if date_str:
                    # Try to parse various date formats
                    date_formats = [
                        "%Y-%m-%dT%H:%M:%S.%fZ",
                        "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%dT%H:%M:%S",
                        "%Y-%m-%d",
                        "%b %d, %Y",
                        "%B %d, %Y",
                    ]

                    for fmt in date_formats:
                        try:
                            date = datetime.strptime(date_str, fmt)
                            return date.replace(tzinfo=pytz.UTC)
                        except ValueError:
                            continue

        # If no date found, use current time
        logger.warning(f"Could not extract date for article: {article_url}")
        return datetime.now(pytz.UTC)

    except Exception as e:
        logger.warning(f"Error fetching article page {article_url}: {str(e)}")
        return datetime.now(pytz.UTC)


def extract_description_from_article_page(article_url):
    """Fetch article page and extract description/summary."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        # Ensure full URL
        if article_url.startswith('/'):
            article_url = f"https://claude.com{article_url}"

        response = requests.get(article_url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Try to find meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'}) or \
                    soup.find('meta', attrs={'property': 'og:description'})

        if meta_desc and meta_desc.get('content'):
            return meta_desc.get('content')

        # Try to find first paragraph
        paragraphs = soup.find_all('p')
        for p in paragraphs:
            text = p.get_text(strip=True)
            if text and len(text) > 50:
                return text[:500]

        return ""

    except Exception as e:
        logger.warning(f"Error fetching article description {article_url}: {str(e)}")
        return ""


def validate_article(article):
    """Validate that article has all required fields with reasonable values."""
    if not article.get("title") or len(article["title"]) < 5:
        logger.warning(f"Invalid title for article: {article.get('link', 'unknown')}")
        return False

    if not article.get("link") or not (article["link"].startswith("http") or article["link"].startswith("/")):
        logger.warning(f"Invalid link for article: {article.get('title', 'unknown')}")
        return False

    if not article.get("date"):
        logger.warning(f"Missing date for article: {article.get('title', 'unknown')}")
        return False

    return True


def parse_blog_html(html_content):
    """Parse the blog HTML content and extract article information."""
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        articles = []
        seen_links = set()
        unknown_structures = 0

        # Find all links that point to blog articles
        all_blog_links = soup.select('a[href*="/blog/"]')

        logger.info(f"Found {len(all_blog_links)} potential blog article links")

        for card in all_blog_links:
            href = card.get("href", "")
            if not href:
                continue

            # Build full URL
            link = f"https://claude.com{href}" if href.startswith("/") else href

            # Skip duplicates
            if link in seen_links:
                continue

            # Skip the main blog page link and category pages
            if link.endswith("/blog") or link.endswith("/blog/") or "/category/" in link:
                continue

            seen_links.add(link)

            # Extract title using fallback chain
            title = extract_title(card)
            if not title:
                logger.debug(f"Could not extract title for link: {link}")
                unknown_structures += 1
                continue

            # Skip if this looks like a navigation element
            if title in ["Blog", "Home", "Read more", "Learn more"]:
                continue

            logger.info(f"Processing article: {title}")

            # Extract date from article page
            date = extract_date_from_article_page(link)

            # Extract description from article page
            description = extract_description_from_article_page(link)
            if not description:
                description = title

            # Create article object
            article = {
                "title": title,
                "link": link,
                "date": date,
                "description": description,
                "category": "Blog",
            }

            # Validate article before adding
            if validate_article(article):
                articles.append(article)
            else:
                unknown_structures += 1

        if unknown_structures > 0:
            logger.warning(f"Encountered {unknown_structures} links with unknown or invalid structures")

        logger.info(f"Successfully parsed {len(articles)} valid articles")
        return articles

    except Exception as e:
        logger.error(f"Error parsing HTML content: {str(e)}")
        raise


def generate_rss_feed(articles, feed_name="claude_blog"):
    """Generate RSS feed from blog articles."""
    try:
        fg = FeedGenerator()
        fg.title("Claude Blog")
        fg.description("Latest posts from the Claude Blog")
        fg.link(href="https://claude.com/blog")
        fg.language("en")

        # Set feed metadata
        fg.author({"name": "Claude"})
        fg.logo("https://cdn.prod.website-files.com/6889473510b50328dbb70ae6/68c33859cc6cd903686c66a2_apple-touch-icon.png")
        fg.subtitle("Get practical guidance and best practices for building with Claude")
        fg.link(href="https://claude.com/blog", rel="alternate")
        fg.link(href=f"https://claude.com/blog/feed_{feed_name}.xml", rel="self")

        # Sort articles by date (newest first)
        sorted_articles = sorted(articles, key=lambda x: x["date"], reverse=True)

        # Add entries
        for article in sorted_articles:
            fe = fg.add_entry()
            fe.title(article["title"])
            fe.description(article["description"])
            fe.link(href=article["link"])
            fe.published(article["date"])
            fe.category(term=article["category"])
            fe.id(article["link"])

        logger.info("Successfully generated RSS feed")
        return fg

    except Exception as e:
        logger.error(f"Error generating RSS feed: {str(e)}")
        raise


def save_rss_feed(feed_generator, feed_name="claude_blog"):
    """Save the RSS feed to a file in the feeds directory."""
    try:
        # Ensure feeds directory exists and get its path
        feeds_dir = ensure_feeds_directory()

        # Create the output file path
        output_filename = feeds_dir / f"feed_{feed_name}.xml"

        # Save the feed
        feed_generator.rss_file(str(output_filename), pretty=True)
        logger.info(f"Successfully saved RSS feed to {output_filename}")
        return output_filename

    except Exception as e:
        logger.error(f"Error saving RSS feed: {str(e)}")
        raise


def main(feed_name="claude_blog"):
    """Main function to generate RSS feed from Claude's blog."""
    try:
        # Fetch blog content
        html_content = fetch_blog_content()

        # Parse articles from HTML
        articles = parse_blog_html(html_content)

        if not articles:
            logger.warning("No articles found! The blog structure may have changed.")
            return False

        # Generate RSS feed with all articles
        feed = generate_rss_feed(articles, feed_name)

        # Save feed to file
        output_file = save_rss_feed(feed, feed_name)

        logger.info(f"Successfully generated RSS feed with {len(articles)} articles")
        return True

    except Exception as e:
        logger.error(f"Failed to generate RSS feed: {str(e)}")
        return False


if __name__ == "__main__":
    main()
