"""
Financial news aggregation and rule-based sentiment analysis pipeline.

This module provides real-time headline monitoring, ticker extraction,
sentiment scoring, and price impact correlation for the MDRAP platform.
"""

import enum
import time
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


class SentimentScore(str, enum.Enum):
    VERY_BEARISH = 'VERY_BEARISH'
    BEARISH = 'BEARISH'
    NEUTRAL = 'NEUTRAL'
    BULLISH = 'BULLISH'
    VERY_BULLISH = 'VERY_BULLISH'


@dataclass(slots=True)
class NewsItem:
    headline: str
    source: str
    timestamp: float
    url: str = ''
    symbols: list[str] = field(default_factory=list)
    sentiment: SentimentScore = SentimentScore.NEUTRAL
    sentiment_score: float = 0.0
    keywords: list[str] = field(default_factory=list)
    urgency: str = 'LOW'
    category: str = ''


class FinancialSentimentAnalyzer:
    BULLISH_WORDS = {
        'beat', 'beats', 'beating', 'exceeded', 'exceeds', 'exceeding',
        'raised', 'raises', 'raising', 'upgrade', 'upgrades', 'upgraded',
        'buy', 'buys', 'buying', 'outperform', 'outperforms', 'outperformed',
        'record', 'growth', 'grow', 'grows', 'growing',
        'rally', 'rallies', 'rallying', 'surge', 'surges', 'surging',
        'breakthrough', 'approved', 'approval', 'approves',
        'acquisition', 'acquire', 'acquires', 'acquired',
        'dividend', 'dividends', 'buyback', 'buybacks', 'profit', 'profits', 'profitable'
    }
    BEARISH_WORDS = {
        'miss', 'misses', 'missing', 'missed',
        'decline', 'declines', 'declining', 'declined',
        'downgrade', 'downgrades', 'downgraded',
        'sell', 'sells', 'selling', 'sold',
        'underperform', 'underperforms', 'underperformed',
        'loss', 'losses', 'losing',
        'warning', 'warns', 'warned',
        'recall', 'recalls', 'recalled',
        'lawsuit', 'lawsuits', 'sued', 'suing',
        'investigation', 'investigations', 'investigated',
        'default', 'defaults', 'defaulted',
        'bankruptcy', 'bankrupt',
        'layoff', 'layoffs', 'laying off',
        'cut', 'cuts', 'cutting',
        'plunge', 'plunges', 'plunging', 'plunged',
        'crash', 'crashes', 'crashing', 'crashed'
    }
    URGENCY_WORDS = {'breaking', 'urgent', 'flash', 'alert', 'emergency', 'halted'}
    
    # Simple category mapping
    CATEGORY_PATTERNS = {
        'EARNINGS': {'earnings', 'revenue', 'eps', 'beat', 'miss', 'profit', 'loss'},
        'FDA': {'fda', 'approved', 'trial', 'clinical', 'drug'},
        'MERGER': {'merger', 'acquisition', 'buyout', 'takeover'},
        'MACRO': {'fed', 'inflation', 'cpi', 'jobs', 'unemployment', 'rate'},
    }

    def analyze(self, headline: str) -> tuple[SentimentScore, float, str, list[str]]:
        """Returns (sentiment, score, urgency, keywords)."""
        words = re.findall(r'\b\w+\b', headline.lower())
        
        bullish_count = 0
        bearish_count = 0
        matched_keywords = []
        urgency = 'LOW'
        
        for w in words:
            if w in self.BULLISH_WORDS:
                bullish_count += 1
                matched_keywords.append(w)
            elif w in self.BEARISH_WORDS:
                bearish_count += 1
                matched_keywords.append(w)
                
            if w in self.URGENCY_WORDS:
                urgency = 'HIGH'
                
        total_count = bullish_count + bearish_count
        score = 0.0
        if total_count > 0:
            score = (bullish_count - bearish_count) / total_count
            
        if score <= -0.5:
            sentiment = SentimentScore.VERY_BEARISH
        elif score < 0.0:
            sentiment = SentimentScore.BEARISH
        elif score == 0.0:
            sentiment = SentimentScore.NEUTRAL
        elif score < 0.5:
            sentiment = SentimentScore.BULLISH
        else:
            sentiment = SentimentScore.VERY_BULLISH
            
        # Optional: check if 'alert', 'breaking' or 'emergency' exists to escalate to 'CRITICAL'
        if any(w in words for w in ['breaking', 'emergency', 'flash']):
            urgency = 'CRITICAL'
            
        return sentiment, score, urgency, matched_keywords


class TickerExtractor:
    KNOWN_TICKERS = {'AAPL', 'MSFT', 'GOOGL', 'GOOG', 'AMZN', 'META', 'TSLA', 'NVDA', 'JPM', 'BAC', 'GS', 'WFC', 'XOM', 'CVX', 'PFE', 'JNJ', 'UNH', 'V', 'MA', 'DIS', 'NFLX', 'AMD', 'INTC', 'CRM', 'ORCL', 'AVGO', 'COST', 'WMT', 'HD', 'MCD', 'BA', 'CAT', 'GE', 'MMM', 'SPY', 'QQQ', 'IWM', 'BTC', 'ETH', 'SOL'}
    
    def extract(self, text: str) -> list[str]:
        """Extract ticker symbols from text. Looks for $TICKER, (TICKER:), and known standalone tickers."""
        tickers = set()
        
        # Look for $TICKER
        for match in re.finditer(r'\$([A-Z]{1,5})\b', text):
            tickers.add(match.group(1))
            
        # Look for (EXCHANGE:TICKER) or (TICKER)
        for match in re.finditer(r'\(\s*(?:[A-Z]+:)?([A-Z]{1,5})\s*\)', text):
            tickers.add(match.group(1))
            
        # Look for known tickers as uppercase words
        words = re.findall(r'\b[A-Z]{1,5}\b', text)
        for w in words:
            if w in self.KNOWN_TICKERS:
                tickers.add(w)
                
        return list(tickers)


class NewsFeed:
    """Aggregates news from RSS/Atom feeds and manual entries."""
    def __init__(self) -> None:
        self._items: list[NewsItem] = []
        self._analyzer = FinancialSentimentAnalyzer()
        self._extractor = TickerExtractor()
    
    def add_headline(self, headline: str, source: str = 'MANUAL', timestamp: float | None = None, url: str = '') -> NewsItem:
        """Add a headline, auto-analyze sentiment and extract tickers."""
        if timestamp is None:
            timestamp = time.time()
            
        sentiment, score, urgency, keywords = self._analyzer.analyze(headline)
        symbols = self._extractor.extract(headline)
        
        # Basic category assignment
        category = ''
        headline_lower = headline.lower()
        for cat, words in self._analyzer.CATEGORY_PATTERNS.items():
            if any(w in headline_lower for w in words):
                category = cat
                break
                
        item = NewsItem(
            headline=headline,
            source=source,
            timestamp=timestamp,
            url=url,
            symbols=symbols,
            sentiment=sentiment,
            sentiment_score=score,
            keywords=keywords,
            urgency=urgency,
            category=category
        )
        self._items.append(item)
        return item
    
    def parse_rss_xml(self, xml_content: str, source: str = 'RSS') -> list[NewsItem]:
        """Parse RSS/Atom XML feed and add all items. Uses xml.etree.ElementTree."""
        new_items = []
        try:
            root = ET.fromstring(xml_content)
            # Basic RSS 2.0 parsing
            for item in root.findall('.//item'):
                title = item.find('title')
                link = item.find('link')
                if title is not None and title.text:
                    url = link.text if link is not None and link.text else ''
                    new_items.append(self.add_headline(title.text, source=source, url=url))
                    
            # Basic Atom parsing
            for entry in root.findall('.//{http://www.w3.org/2005/Atom}entry'):
                title = entry.find('{http://www.w3.org/2005/Atom}title')
                link = entry.find('{http://www.w3.org/2005/Atom}link')
                if title is not None and title.text:
                    url = link.attrib.get('href', '') if link is not None else ''
                    new_items.append(self.add_headline(title.text, source=source, url=url))
        except ET.ParseError:
            pass
        return new_items
    
    def query(self, symbol: str | None = None, sentiment: SentimentScore | None = None, since: float | None = None, limit: int = 50) -> list[NewsItem]:
        """Query stored news items with optional filters."""
        results = self._items
        
        if since is not None:
            results = [item for item in results if item.timestamp >= since]
            
        if sentiment is not None:
            results = [item for item in results if item.sentiment == sentiment]
            
        if symbol is not None:
            symbol_upper = symbol.upper()
            results = [item for item in results if symbol_upper in item.symbols]
            
        # Sort by timestamp descending
        results.sort(key=lambda x: x.timestamp, reverse=True)
        return results[:limit]
    
    def sentiment_summary(self, symbol: str | None = None) -> dict[str, float | int]:
        """Return aggregate sentiment stats: total items, bullish/bearish/neutral counts, avg score."""
        items = self.query(symbol=symbol, limit=1000000)
        
        total = len(items)
        if total == 0:
            return {
                'total': 0,
                'bullish': 0,
                'bearish': 0,
                'neutral': 0,
                'avg_score': 0.0
            }
            
        bullish = sum(1 for i in items if i.sentiment in {SentimentScore.BULLISH, SentimentScore.VERY_BULLISH})
        bearish = sum(1 for i in items if i.sentiment in {SentimentScore.BEARISH, SentimentScore.VERY_BEARISH})
        neutral = sum(1 for i in items if i.sentiment == SentimentScore.NEUTRAL)
        avg_score = sum(i.sentiment_score for i in items) / total
        
        return {
            'total': total,
            'bullish': bullish,
            'bearish': bearish,
            'neutral': neutral,
            'avg_score': avg_score
        }
    
    def correlate_with_price(self, news_item: NewsItem, price_before: float, price_after: float) -> dict[str, float | bool]:
        """Compute price impact of a news event. Returns dict with price_change_pct, direction_match."""
        if price_before <= 0.0:
            return {'price_change_pct': 0.0, 'direction_match': False}
            
        change_pct = ((price_after - price_before) / price_before) * 100.0
        
        direction_match = False
        if change_pct > 0 and news_item.sentiment_score > 0:
            direction_match = True
        elif change_pct < 0 and news_item.sentiment_score < 0:
            direction_match = True
        elif change_pct == 0 and news_item.sentiment_score == 0:
            direction_match = True
            
        return {
            'price_change_pct': change_pct,
            'direction_match': direction_match
        }
    
    @property
    def latest(self) -> list[NewsItem]:
        """Last 10 news items sorted by timestamp descending."""
        return sorted(self._items, key=lambda x: x.timestamp, reverse=True)[:10]
