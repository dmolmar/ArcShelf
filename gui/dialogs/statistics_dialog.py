# gui/dialogs/statistics_dialog.py
"""
Statistics Dashboard Dialog - Shows comprehensive statistics for current search results.
"""

import os
import re
import datetime
from typing import TYPE_CHECKING, List, Dict, Tuple, Optional, Any
from collections import defaultdict, Counter
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QLabel,
    QSlider, QFrame, QScrollArea, QGridLayout, QSizePolicy, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThreadPool
from PyQt6.QtGui import QFont, QPixmap

import matplotlib
matplotlib.use('QtAgg')  # Use Qt backend
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

from utils.workers import Worker

if TYPE_CHECKING:
    from gui.main_window import ImageGallery
    from database.db_manager import Database
    from image_processing.thumbnail import ThumbnailCache

# Dark theme colors
COLORS = {
    'bg': '#1e1e2e',
    'bg_light': '#2a2a3e',
    'text': '#e2e8f0',
    'text_dim': '#94a3b8',
    'primary': '#6366f1',
    'secondary': '#ec4899',
    'accent': '#10b981',
    'warning': '#f59e0b',
    'danger': '#ef4444',
    'chart_colors': ['#6366f1', '#ec4899', '#10b981', '#f59e0b', '#3b82f6', '#8b5cf6', '#14b8a6', '#f97316']
}


def apply_dark_style(fig: Figure, ax=None):
    """Apply dark theme to matplotlib figure and axes."""
    fig.patch.set_facecolor(COLORS['bg'])
    if ax is not None:
        if hasattr(ax, '__iter__'):
            for a in ax:
                _style_axis(a)
        else:
            _style_axis(ax)


def _style_axis(ax):
    """Style a single axis with dark theme."""
    ax.set_facecolor(COLORS['bg_light'])
    ax.tick_params(colors=COLORS['text_dim'], labelsize=9)
    ax.xaxis.label.set_color(COLORS['text'])
    ax.yaxis.label.set_color(COLORS['text'])
    ax.title.set_color(COLORS['text'])
    for spine in ax.spines.values():
        spine.set_color(COLORS['text_dim'])
        spine.set_linewidth(0.5)


class StyledCard(QFrame):
    """A styled card widget for displaying stats."""
    
    def __init__(self, title: str, value: str, icon: str = "", color: str = COLORS['primary']):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {COLORS['bg_light']};
                border-radius: 12px;
                padding: 16px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        
        # Icon + Title
        title_label = QLabel(f"{icon} {title}")
        title_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 12px;")
        layout.addWidget(title_label)
        
        # Value
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: bold;")
        layout.addWidget(self.value_label)
    
    def set_value(self, value: str):
        self.value_label.setText(value)


class StatisticsDialog(QDialog):
    """Multi-tab statistics dashboard dialog."""
    
    def __init__(self, parent: 'ImageGallery', db: 'Database', 
                 current_image_paths: List[str], thumbnail_cache: 'ThumbnailCache'):
        super().__init__(parent)
        self.db = db
        self.image_paths = current_image_paths
        self.thumbnail_cache = thumbnail_cache
        self.threadpool = QThreadPool()
        
        # Cached statistics data
        self.stats_data: Dict[str, Any] = {}
        self.all_tags_with_counts: List[Tuple[str, int]] = []
        
        self.setWindowTitle("📊 Collection Statistics")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        
        # Apply dark theme to dialog
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {COLORS['bg']};
            }}
            QTabWidget::pane {{
                border: 1px solid {COLORS['bg_light']};
                background-color: {COLORS['bg']};
            }}
            QTabBar::tab {{
                background-color: {COLORS['bg_light']};
                color: {COLORS['text_dim']};
                padding: 10px 20px;
                margin-right: 2px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }}
            QTabBar::tab:selected {{
                background-color: {COLORS['primary']};
                color: {COLORS['text']};
            }}
            QLabel {{
                color: {COLORS['text']};
            }}
            QSlider::groove:horizontal {{
                height: 8px;
                background: {COLORS['bg_light']};
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {COLORS['primary']};
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }}
            QSlider::sub-page:horizontal {{
                background: {COLORS['primary']};
                border-radius: 4px;
            }}
            QScrollArea {{
                border: none;
                background-color: {COLORS['bg']};
            }}
            QScrollArea > QWidget > QWidget {{
                background-color: {COLORS['bg']};
            }}
        """)
        
        self.setup_ui()
        self.load_statistics()
    
    def setup_ui(self):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Header
        header = QLabel(f"Statistics for {len(self.image_paths)} images")
        header.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {COLORS['text']}; margin-bottom: 8px;")
        layout.addWidget(header)
        
        # Loading indicator
        self.loading_label = QLabel("Loading statistics...")
        self.loading_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 14px;")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.loading_label)
        
        # Tab widget (hidden until loaded)
        self.tab_widget = QTabWidget()
        self.tab_widget.hide()
        layout.addWidget(self.tab_widget)
        
        # Create tabs
        self.overview_tab = self._create_overview_tab()
        self.tags_tab = self._create_tags_tab()
        self.timeline_tab = self._create_timeline_tab()
        self.quality_tab = self._create_quality_tab()
        self.fun_tab = self._create_fun_tab()
        
        self.tab_widget.addTab(self.overview_tab, "📊 Overview")
        self.tab_widget.addTab(self.tags_tab, "🏷️ Tags")
        self.tab_widget.addTab(self.timeline_tab, "📅 Timeline")
        self.tab_widget.addTab(self.quality_tab, "🖼️ Quality")
        self.tab_widget.addTab(self.fun_tab, "🎉 Fun Stats")
    
    def _create_scrollable_tab(self) -> Tuple[QScrollArea, QWidget, QVBoxLayout]:
        """Create a scrollable tab with a content widget."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(8, 8, 8, 8)
        
        scroll.setWidget(content)
        return scroll, content, layout
    
    def _create_overview_tab(self) -> QWidget:
        """Create the Overview tab."""
        scroll, content, layout = self._create_scrollable_tab()
        
        # Cards row
        cards_layout = QHBoxLayout()
        self.total_images_card = StyledCard("Total Images", "...", "📷", COLORS['primary'])
        self.total_storage_card = StyledCard("Total Storage", "...", "💾", COLORS['secondary'])
        self.date_range_card = StyledCard("Date Range", "...", "📅", COLORS['accent'])
        self.avg_tags_card = StyledCard("Avg Tags/Image", "...", "🏷️", COLORS['warning'])
        
        cards_layout.addWidget(self.total_images_card)
        cards_layout.addWidget(self.total_storage_card)
        cards_layout.addWidget(self.date_range_card)
        cards_layout.addWidget(self.avg_tags_card)
        layout.addLayout(cards_layout)
        
        # Charts row
        charts_layout = QHBoxLayout()
        
        # Rating distribution donut
        self.rating_figure = Figure(figsize=(4, 3), dpi=100)
        self.rating_canvas = FigureCanvas(self.rating_figure)
        self.rating_canvas.setMinimumHeight(250)
        charts_layout.addWidget(self.rating_canvas)
        
        # File format pie
        self.format_figure = Figure(figsize=(4, 3), dpi=100)
        self.format_canvas = FigureCanvas(self.format_figure)
        self.format_canvas.setMinimumHeight(250)
        charts_layout.addWidget(self.format_canvas)
        
        # Aspect ratio bar
        self.aspect_figure = Figure(figsize=(4, 3), dpi=100)
        self.aspect_canvas = FigureCanvas(self.aspect_figure)
        self.aspect_canvas.setMinimumHeight(250)
        charts_layout.addWidget(self.aspect_canvas)
        
        layout.addLayout(charts_layout)
        layout.addStretch(1)
        
        return scroll
    
    def _create_tags_tab(self) -> QWidget:
        """Create the Tags Analysis tab."""
        scroll, content, layout = self._create_scrollable_tab()
        
        # Slider for filtering common tags
        slider_frame = QFrame()
        slider_frame.setStyleSheet(f"background-color: {COLORS['bg_light']}; border-radius: 8px; padding: 12px;")
        slider_layout = QVBoxLayout(slider_frame)
        
        slider_header = QHBoxLayout()
        slider_label = QLabel("🎚️ Exclude Top Common Tags:")
        slider_label.setStyleSheet(f"font-weight: bold;")
        self.slider_value_label = QLabel("0%")
        self.slider_value_label.setStyleSheet(f"color: {COLORS['primary']}; font-weight: bold;")
        slider_header.addWidget(slider_label)
        slider_header.addStretch()
        slider_header.addWidget(self.slider_value_label)
        slider_layout.addLayout(slider_header)
        
        self.tag_filter_slider = QSlider(Qt.Orientation.Horizontal)
        self.tag_filter_slider.setRange(0, 100)
        self.tag_filter_slider.setValue(0)
        self.tag_filter_slider.valueChanged.connect(self._on_tag_filter_changed)
        slider_layout.addWidget(self.tag_filter_slider)
        
        layout.addWidget(slider_frame)
        
        # Tags chart
        self.tags_figure = Figure(figsize=(10, 5), dpi=100)
        self.tags_canvas = FigureCanvas(self.tags_figure)
        self.tags_canvas.setMinimumHeight(400)
        layout.addWidget(self.tags_canvas)
        
        # Tag category distribution
        category_layout = QHBoxLayout()
        
        self.category_figure = Figure(figsize=(5, 4), dpi=100)
        self.category_canvas = FigureCanvas(self.category_figure)
        self.category_canvas.setMinimumHeight(300)
        category_layout.addWidget(self.category_canvas)
        
        # Tag stats cards
        tag_stats_layout = QVBoxLayout()
        self.unique_tags_card = StyledCard("Unique Tags", "...", "🏷️", COLORS['primary'])
        self.manual_tags_card = StyledCard("Manual Tags", "...", "✍️", COLORS['secondary'])
        tag_stats_layout.addWidget(self.unique_tags_card)
        tag_stats_layout.addWidget(self.manual_tags_card)
        tag_stats_layout.addStretch()
        category_layout.addLayout(tag_stats_layout)
        
        layout.addLayout(category_layout)
        layout.addStretch(1)
        
        return scroll
    
    def _create_timeline_tab(self) -> QWidget:
        """Create the Timeline & Trends tab."""
        scroll, content, layout = self._create_scrollable_tab()
        
        # Monthly chart
        self.monthly_figure = Figure(figsize=(10, 4), dpi=100)
        self.monthly_canvas = FigureCanvas(self.monthly_figure)
        self.monthly_canvas.setMinimumHeight(300)
        layout.addWidget(self.monthly_canvas)
        
        # Weekday + Hour charts
        time_layout = QHBoxLayout()
        
        self.weekday_figure = Figure(figsize=(5, 3), dpi=100)
        self.weekday_canvas = FigureCanvas(self.weekday_figure)
        self.weekday_canvas.setMinimumHeight(250)
        time_layout.addWidget(self.weekday_canvas)
        
        self.hour_figure = Figure(figsize=(5, 3), dpi=100)
        self.hour_canvas = FigureCanvas(self.hour_figure)
        self.hour_canvas.setMinimumHeight(250)
        time_layout.addWidget(self.hour_canvas)
        
        layout.addLayout(time_layout)
        
        # Cumulative growth
        self.cumulative_figure = Figure(figsize=(10, 3), dpi=100)
        self.cumulative_canvas = FigureCanvas(self.cumulative_figure)
        self.cumulative_canvas.setMinimumHeight(250)
        layout.addWidget(self.cumulative_canvas)
        
        layout.addStretch(1)
        return scroll
    
    def _create_quality_tab(self) -> QWidget:
        """Create the Quality & Sources tab."""
        scroll, content, layout = self._create_scrollable_tab()
        
        # Resolution + Megapixels
        res_layout = QHBoxLayout()
        
        self.resolution_figure = Figure(figsize=(5, 4), dpi=100)
        self.resolution_canvas = FigureCanvas(self.resolution_figure)
        self.resolution_canvas.setMinimumHeight(300)
        res_layout.addWidget(self.resolution_canvas)
        
        self.megapixel_figure = Figure(figsize=(5, 4), dpi=100)
        self.megapixel_canvas = FigureCanvas(self.megapixel_figure)
        self.megapixel_canvas.setMinimumHeight(300)
        res_layout.addWidget(self.megapixel_canvas)
        
        layout.addLayout(res_layout)
        
        # Source detection + File size
        source_layout = QHBoxLayout()
        
        self.source_figure = Figure(figsize=(5, 4), dpi=100)
        self.source_canvas = FigureCanvas(self.source_figure)
        self.source_canvas.setMinimumHeight(300)
        source_layout.addWidget(self.source_canvas)
        
        self.filesize_figure = Figure(figsize=(5, 4), dpi=100)
        self.filesize_canvas = FigureCanvas(self.filesize_figure)
        self.filesize_canvas.setMinimumHeight(300)
        source_layout.addWidget(self.filesize_canvas)
        
        layout.addLayout(source_layout)
        
        # Resolution over time
        self.res_time_figure = Figure(figsize=(10, 3), dpi=100)
        self.res_time_canvas = FigureCanvas(self.res_time_figure)
        self.res_time_canvas.setMinimumHeight(250)
        layout.addWidget(self.res_time_canvas)
        
        layout.addStretch(1)
        return scroll
    
    def _create_fun_tab(self) -> QWidget:
        """Create the Fun Stats tab."""
        scroll, content, layout = self._create_scrollable_tab()
        
        # Grid of fun stats
        grid = QGridLayout()
        grid.setSpacing(16)
        
        self.most_tagged_card = StyledCard("Most Tagged", "...", "🏆", COLORS['primary'])
        self.least_tagged_card = StyledCard("Least Tagged", "...", "🔖", COLORS['text_dim'])
        self.oldest_card = StyledCard("First Image", "...", "📅", COLORS['accent'])
        self.newest_card = StyledCard("Latest Image", "...", "🆕", COLORS['secondary'])
        self.largest_card = StyledCard("Largest File", "...", "💾", COLORS['warning'])
        self.smallest_card = StyledCard("Smallest File", "...", "🐜", COLORS['text_dim'])
        self.diversity_card = StyledCard("Diversity Score", "...", "🎨", COLORS['primary'])
        
        grid.addWidget(self.most_tagged_card, 0, 0)
        grid.addWidget(self.least_tagged_card, 0, 1)
        grid.addWidget(self.oldest_card, 0, 2)
        grid.addWidget(self.newest_card, 1, 0)
        grid.addWidget(self.largest_card, 1, 1)
        grid.addWidget(self.smallest_card, 1, 2)
        grid.addWidget(self.diversity_card, 2, 0)
        
        layout.addLayout(grid)
        
        # Tag co-occurrence
        self.cooccurrence_figure = Figure(figsize=(10, 5), dpi=100)
        self.cooccurrence_canvas = FigureCanvas(self.cooccurrence_figure)
        self.cooccurrence_canvas.setMinimumHeight(350)
        layout.addWidget(self.cooccurrence_canvas)
        
        layout.addStretch(1)
        return scroll
    
    def _on_tag_filter_changed(self, value: int):
        """Handle tag filter slider change."""
        self.slider_value_label.setText(f"{value}%")
        self._update_tags_chart()
    
    def load_statistics(self):
        """Load statistics in background thread."""
        def fetch_stats():
            return self._compute_statistics()
        
        def on_stats_ready(stats):
            self.stats_data = stats
            self.all_tags_with_counts = stats.get('all_tags', [])
            self.loading_label.hide()
            self.tab_widget.show()
            self._populate_all_charts()
        
        def on_error(error):
            self.loading_label.setText(f"Error loading statistics: {error[1]}")
        
        worker = Worker(fetch_stats)
        worker.signals.finished.connect(on_stats_ready)
        worker.signals.error.connect(on_error)
        self.threadpool.start(worker)
    
    def _compute_statistics(self) -> Dict[str, Any]:
        """Compute all statistics from image paths."""
        stats = {}
        
        if not self.image_paths:
            return stats
        
        # Basic counts
        stats['total_images'] = len(self.image_paths)
        
        # File-based stats
        total_size = 0
        mod_times = []
        resolutions = []
        file_formats = defaultdict(int)
        aspect_ratios = {'portrait': 0, 'landscape': 0, 'square': 0}
        megapixels = []
        file_sizes = []
        sources = defaultdict(int)
        weekdays = defaultdict(int)
        hours = defaultdict(int)
        monthly = defaultdict(int)
        resolution_over_time = []  # (date, megapixels)
        
        for path in self.image_paths:
            try:
                if os.path.exists(path):
                    size = os.path.getsize(path)
                    total_size += size
                    file_sizes.append(size / (1024 * 1024))  # MB
                    
                    mtime = os.path.getmtime(path)
                    dt = datetime.datetime.fromtimestamp(mtime)
                    mod_times.append(dt)
                    
                    # Weekday and hour
                    weekdays[dt.weekday()] += 1
                    hours[dt.hour] += 1
                    
                    # Monthly
                    month_key = dt.strftime('%Y-%m')
                    monthly[month_key] += 1
                    
                    # File format
                    ext = Path(path).suffix.lower()
                    file_formats[ext] += 1
                    
                    # Source detection from filename
                    source = self._detect_source(Path(path).stem)
                    sources[source] += 1
                    
            except Exception:
                pass
        
        # Get resolution data from database
        resolutions_dict = self.db.get_resolutions_for_paths(self.image_paths)
        for path, res in resolutions_dict.items():
            if res and 'x' in res:
                try:
                    w, h = map(int, res.split('x'))
                    mp = (w * h) / 1_000_000
                    megapixels.append(mp)
                    resolutions.append((w, h))
                    
                    # Aspect ratio
                    ratio = w / h if h > 0 else 1
                    if ratio > 1.1:
                        aspect_ratios['landscape'] += 1
                    elif ratio < 0.9:
                        aspect_ratios['portrait'] += 1
                    else:
                        aspect_ratios['square'] += 1
                    
                    # Resolution over time
                    if path in self.image_paths:
                        try:
                            mtime = os.path.getmtime(path)
                            resolution_over_time.append((datetime.datetime.fromtimestamp(mtime), mp))
                        except:
                            pass
                except:
                    pass
        
        stats['total_size'] = total_size
        stats['min_date'] = min(mod_times) if mod_times else None
        stats['max_date'] = max(mod_times) if mod_times else None
        stats['file_formats'] = dict(file_formats)
        stats['aspect_ratios'] = aspect_ratios
        stats['megapixels'] = megapixels
        stats['file_sizes'] = file_sizes
        stats['sources'] = dict(sources)
        stats['weekdays'] = dict(weekdays)
        stats['hours'] = dict(hours)
        stats['monthly'] = dict(sorted(monthly.items()))
        stats['resolution_over_time'] = sorted(resolution_over_time, key=lambda x: x[0])
        stats['resolutions'] = resolutions
        
        # Resolution buckets
        res_buckets = {'< 720p': 0, '720p-1080p': 0, '1080p-1440p': 0, '1440p-4K': 0, '4K+': 0}
        for w, h in resolutions:
            pixels = w * h
            if pixels < 1280 * 720:
                res_buckets['< 720p'] += 1
            elif pixels < 1920 * 1080:
                res_buckets['720p-1080p'] += 1
            elif pixels < 2560 * 1440:
                res_buckets['1080p-1440p'] += 1
            elif pixels < 3840 * 2160:
                res_buckets['1440p-4K'] += 1
            else:
                res_buckets['4K+'] += 1
        stats['resolution_buckets'] = res_buckets
        
        # Tag statistics
        tag_counts = defaultdict(int)
        tag_categories = defaultdict(int)
        manual_tag_count = 0
        image_tag_counts = []
        rating_counts = defaultdict(int)
        image_tags_map = {}  # For co-occurrence
        
        most_tagged = (None, 0)
        least_tagged = (None, float('inf'))
        
        for path in self.image_paths:
            rating, tags = self.db.get_image_info_by_path(path)
            if rating:
                rating_counts[rating] += 1
            
            tag_count = len(tags)
            image_tag_counts.append(tag_count)
            
            if tag_count > most_tagged[1]:
                most_tagged = (path, tag_count)
            if tag_count < least_tagged[1] and tag_count > 0:
                least_tagged = (path, tag_count)
            
            image_tags_set = set()
            for tag in tags:
                tag_counts[tag.tag] += 1
                tag_categories[tag.category] += 1
                if tag.is_manual:
                    manual_tag_count += 1
                image_tags_set.add(tag.tag)
            
            image_tags_map[path] = image_tags_set
        
        stats['rating_counts'] = dict(rating_counts)
        stats['all_tags'] = sorted(tag_counts.items(), key=lambda x: -x[1])
        stats['tag_categories'] = dict(tag_categories)
        stats['manual_tag_count'] = manual_tag_count
        stats['unique_tags'] = len(tag_counts)
        stats['avg_tags'] = sum(image_tag_counts) / len(image_tag_counts) if image_tag_counts else 0
        stats['most_tagged'] = most_tagged
        stats['least_tagged'] = least_tagged if least_tagged[0] else (None, 0)
        
        # Diversity score (0-100 based on unique tags / total tag uses)
        total_tag_uses = sum(tag_counts.values())
        if total_tag_uses > 0:
            diversity = min(100, (len(tag_counts) / total_tag_uses) * 500)
        else:
            diversity = 0
        stats['diversity_score'] = diversity
        
        # Tag co-occurrence (top pairs)
        cooccurrence = defaultdict(int)
        for path, tags in image_tags_map.items():
            tags_list = sorted(list(tags))[:20]  # Limit for performance
            for i, t1 in enumerate(tags_list):
                for t2 in tags_list[i+1:]:
                    cooccurrence[(t1, t2)] += 1
        
        stats['cooccurrence'] = sorted(cooccurrence.items(), key=lambda x: -x[1])[:20]
        
        # File extremes
        largest = (None, 0)
        smallest = (None, float('inf'))
        for path in self.image_paths:
            try:
                size = os.path.getsize(path)
                if size > largest[1]:
                    largest = (path, size)
                if size < smallest[1]:
                    smallest = (path, size)
            except:
                pass
        
        stats['largest_file'] = largest
        stats['smallest_file'] = smallest if smallest[0] else (None, 0)
        
        return stats
    
    def _detect_source(self, filename: str) -> str:
        """Detect image source from filename patterns."""
        if re.search(r'_p\d+', filename):
            return 'Pixiv'
        elif filename.startswith('__') and re.search(r'__\w+__\w+', filename):
            return 'Danbooru'
        elif re.search(r'\.full\.\d+$', filename):
            return 'Zerochan'
        elif (re.match(r'^[a-zA-Z0-9\-\_]+$', filename) and 12 <= len(filename) <= 17) or filename.startswith('twitter_'):
            return 'Twitter'
        elif re.match(r'^[a-fA-F0-9]{32}$', filename):
            return 'Pinterest'
        else:
            return 'Other'
    
    def _populate_all_charts(self):
        """Populate all charts with loaded data."""
        if not self.stats_data:
            return
        
        self._update_overview_cards()
        self._update_rating_chart()
        self._update_format_chart()
        self._update_aspect_chart()
        self._update_tags_chart()
        self._update_category_chart()
        self._update_monthly_chart()
        self._update_weekday_chart()
        self._update_hour_chart()
        self._update_cumulative_chart()
        self._update_resolution_chart()
        self._update_megapixel_chart()
        self._update_source_chart()
        self._update_filesize_chart()
        self._update_res_time_chart()
        self._update_fun_stats()
        self._update_cooccurrence_chart()
    
    def _update_overview_cards(self):
        """Update overview stat cards."""
        s = self.stats_data
        
        self.total_images_card.set_value(f"{s.get('total_images', 0):,}")
        
        # Human readable size
        size = s.get('total_size', 0)
        if size > 1024**3:
            size_str = f"{size / 1024**3:.2f} GB"
        elif size > 1024**2:
            size_str = f"{size / 1024**2:.1f} MB"
        else:
            size_str = f"{size / 1024:.0f} KB"
        self.total_storage_card.set_value(size_str)
        
        # Date range
        min_d = s.get('min_date')
        max_d = s.get('max_date')
        if min_d and max_d:
            date_str = f"{min_d.strftime('%Y-%m')} → {max_d.strftime('%Y-%m')}"
        else:
            date_str = "N/A"
        self.date_range_card.set_value(date_str)
        
        self.avg_tags_card.set_value(f"{s.get('avg_tags', 0):.1f}")
    
    def _update_rating_chart(self):
        """Update rating distribution donut chart."""
        self.rating_figure.clear()
        ax = self.rating_figure.add_subplot(111)
        apply_dark_style(self.rating_figure, ax)
        
        ratings = self.stats_data.get('rating_counts', {})
        if ratings:
            labels = list(ratings.keys())
            sizes = list(ratings.values())
            colors = [COLORS['accent'], COLORS['warning'], COLORS['danger']][:len(labels)]
            
            wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct='%1.1f%%',
                                               colors=colors, wedgeprops=dict(width=0.6))
            for text in texts + autotexts:
                text.set_color(COLORS['text'])
            ax.set_title('Rating Distribution', color=COLORS['text'], fontsize=11, fontweight='bold')
        else:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center', color=COLORS['text_dim'])
        
        self.rating_canvas.draw()
    
    def _update_format_chart(self):
        """Update file format pie chart."""
        self.format_figure.clear()
        ax = self.format_figure.add_subplot(111)
        apply_dark_style(self.format_figure, ax)
        
        formats = self.stats_data.get('file_formats', {})
        if formats:
            labels = [k.upper() for k in formats.keys()]
            sizes = list(formats.values())
            colors = COLORS['chart_colors'][:len(labels)]
            
            wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct='%1.1f%%',
                                               colors=colors)
            for text in texts + autotexts:
                text.set_color(COLORS['text'])
            ax.set_title('File Formats', color=COLORS['text'], fontsize=11, fontweight='bold')
        else:
            ax.text(0.5, 0.5, 'No data', ha='center', va='center', color=COLORS['text_dim'])
        
        self.format_canvas.draw()
    
    def _update_aspect_chart(self):
        """Update aspect ratio bar chart."""
        self.aspect_figure.clear()
        ax = self.aspect_figure.add_subplot(111)
        apply_dark_style(self.aspect_figure, ax)
        
        aspects = self.stats_data.get('aspect_ratios', {})
        if aspects:
            labels = ['Portrait', 'Landscape', 'Square']
            sizes = [aspects.get('portrait', 0), aspects.get('landscape', 0), aspects.get('square', 0)]
            colors = [COLORS['secondary'], COLORS['primary'], COLORS['accent']]
            
            bars = ax.barh(labels, sizes, color=colors, height=0.6)
            ax.set_xlabel('Count')
            ax.set_title('Aspect Ratio', color=COLORS['text'], fontsize=11, fontweight='bold')
            
            for bar, val in zip(bars, sizes):
                ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height()/2, 
                       str(val), va='center', color=COLORS['text'], fontsize=9)
        
        self.aspect_figure.tight_layout()
        self.aspect_canvas.draw()
    
    def _update_tags_chart(self):
        """Update top tags bar chart with filter applied."""
        self.tags_figure.clear()
        ax = self.tags_figure.add_subplot(111)
        apply_dark_style(self.tags_figure, ax)
        
        all_tags = self.all_tags_with_counts
        if not all_tags:
            ax.text(0.5, 0.5, 'No tags found', ha='center', va='center', color=COLORS['text_dim'])
            self.tags_canvas.draw()
            return
        
        # Apply filter
        filter_pct = self.tag_filter_slider.value() / 100.0
        n_exclude = int(len(all_tags) * filter_pct)
        filtered_tags = all_tags[n_exclude:][:25]  # Skip top n_exclude, take next 25
        
        if not filtered_tags:
            ax.text(0.5, 0.5, 'All tags filtered out', ha='center', va='center', color=COLORS['text_dim'])
            self.tags_canvas.draw()
            return
        
        labels = [t[0][:30] for t in reversed(filtered_tags)]  # Truncate long names
        counts = [t[1] for t in reversed(filtered_tags)]
        
        colors = plt.cm.viridis(np.linspace(0.3, 0.9, len(labels)))
        ax.barh(labels, counts, color=colors, height=0.7)
        ax.set_xlabel('Count')
        ax.set_title(f'Top Tags (excluding top {self.tag_filter_slider.value()}% common)', 
                    color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.tags_figure.tight_layout()
        self.tags_canvas.draw()
        
        # Update stats cards
        self.unique_tags_card.set_value(str(self.stats_data.get('unique_tags', 0)))
        self.manual_tags_card.set_value(str(self.stats_data.get('manual_tag_count', 0)))
    
    def _update_category_chart(self):
        """Update tag category pie chart."""
        self.category_figure.clear()
        ax = self.category_figure.add_subplot(111)
        apply_dark_style(self.category_figure, ax)
        
        categories = self.stats_data.get('tag_categories', {})
        if categories:
            labels = list(categories.keys())
            sizes = list(categories.values())
            colors = COLORS['chart_colors'][:len(labels)]
            
            wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct='%1.1f%%',
                                               colors=colors)
            for text in texts + autotexts:
                text.set_color(COLORS['text'])
                text.set_fontsize(9)
            ax.set_title('Tag Categories', color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.category_canvas.draw()
    
    def _update_monthly_chart(self):
        """Update monthly images bar chart."""
        self.monthly_figure.clear()
        ax = self.monthly_figure.add_subplot(111)
        apply_dark_style(self.monthly_figure, ax)
        
        monthly = self.stats_data.get('monthly', {})
        if monthly:
            months = list(monthly.keys())
            counts = list(monthly.values())
            
            colors = plt.cm.plasma(np.linspace(0.2, 0.8, len(months)))
            ax.bar(range(len(months)), counts, color=colors)
            
            # Show fewer x-labels if many months
            step = max(1, len(months) // 12)
            ax.set_xticks(range(0, len(months), step))
            ax.set_xticklabels([months[i] for i in range(0, len(months), step)], rotation=45, ha='right')
            
            ax.set_ylabel('Images')
            ax.set_title('Images Added Per Month', color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.monthly_figure.tight_layout()
        self.monthly_canvas.draw()
    
    def _update_weekday_chart(self):
        """Update weekday distribution chart."""
        self.weekday_figure.clear()
        ax = self.weekday_figure.add_subplot(111)
        apply_dark_style(self.weekday_figure, ax)
        
        weekdays = self.stats_data.get('weekdays', {})
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        counts = [weekdays.get(i, 0) for i in range(7)]
        
        colors = COLORS['chart_colors'][:7]
        ax.bar(days, counts, color=colors)
        ax.set_ylabel('Images')
        ax.set_title('Images by Weekday', color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.weekday_figure.tight_layout()
        self.weekday_canvas.draw()
    
    def _update_hour_chart(self):
        """Update hour distribution chart."""
        self.hour_figure.clear()
        ax = self.hour_figure.add_subplot(111)
        apply_dark_style(self.hour_figure, ax)
        
        hours = self.stats_data.get('hours', {})
        hour_labels = list(range(24))
        counts = [hours.get(i, 0) for i in range(24)]
        
        colors = plt.cm.twilight(np.linspace(0, 1, 24))
        ax.bar(hour_labels, counts, color=colors)
        ax.set_xlabel('Hour')
        ax.set_ylabel('Images')
        ax.set_title('Images by Hour of Day', color=COLORS['text'], fontsize=11, fontweight='bold')
        ax.set_xticks([0, 6, 12, 18, 23])
        
        self.hour_figure.tight_layout()
        self.hour_canvas.draw()
    
    def _update_cumulative_chart(self):
        """Update cumulative growth line chart."""
        self.cumulative_figure.clear()
        ax = self.cumulative_figure.add_subplot(111)
        apply_dark_style(self.cumulative_figure, ax)
        
        monthly = self.stats_data.get('monthly', {})
        if monthly:
            months = list(monthly.keys())
            counts = list(monthly.values())
            cumulative = np.cumsum(counts)
            
            ax.fill_between(range(len(months)), cumulative, color=COLORS['primary'], alpha=0.3)
            ax.plot(range(len(months)), cumulative, color=COLORS['primary'], linewidth=2)
            
            step = max(1, len(months) // 12)
            ax.set_xticks(range(0, len(months), step))
            ax.set_xticklabels([months[i] for i in range(0, len(months), step)], rotation=45, ha='right')
            
            ax.set_ylabel('Total Images')
            ax.set_title('Collection Growth Over Time', color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.cumulative_figure.tight_layout()
        self.cumulative_canvas.draw()
    
    def _update_resolution_chart(self):
        """Update resolution buckets bar chart."""
        self.resolution_figure.clear()
        ax = self.resolution_figure.add_subplot(111)
        apply_dark_style(self.resolution_figure, ax)
        
        buckets = self.stats_data.get('resolution_buckets', {})
        if buckets:
            labels = list(buckets.keys())
            counts = list(buckets.values())
            colors = plt.cm.cool(np.linspace(0.2, 0.8, len(labels)))
            
            ax.bar(labels, counts, color=colors)
            ax.set_ylabel('Images')
            ax.set_title('Resolution Buckets', color=COLORS['text'], fontsize=11, fontweight='bold')
            plt.setp(ax.get_xticklabels(), rotation=30, ha='right')
        
        self.resolution_figure.tight_layout()
        self.resolution_canvas.draw()
    
    def _update_megapixel_chart(self):
        """Update megapixel histogram."""
        self.megapixel_figure.clear()
        ax = self.megapixel_figure.add_subplot(111)
        apply_dark_style(self.megapixel_figure, ax)
        
        megapixels = self.stats_data.get('megapixels', [])
        if megapixels:
            ax.hist(megapixels, bins=20, color=COLORS['secondary'], edgecolor=COLORS['bg'], alpha=0.8)
            ax.set_xlabel('Megapixels')
            ax.set_ylabel('Count')
            ax.set_title('Megapixel Distribution', color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.megapixel_figure.tight_layout()
        self.megapixel_canvas.draw()
    
    def _update_source_chart(self):
        """Update source detection pie chart."""
        self.source_figure.clear()
        ax = self.source_figure.add_subplot(111)
        apply_dark_style(self.source_figure, ax)
        
        sources = self.stats_data.get('sources', {})
        if sources:
            labels = list(sources.keys())
            sizes = list(sources.values())
            colors = COLORS['chart_colors'][:len(labels)]
            
            wedges, texts, autotexts = ax.pie(sizes, labels=labels, autopct='%1.1f%%',
                                               colors=colors)
            for text in texts + autotexts:
                text.set_color(COLORS['text'])
                text.set_fontsize(9)
            ax.set_title('Image Sources (Detected)', color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.source_canvas.draw()
    
    def _update_filesize_chart(self):
        """Update file size histogram."""
        self.filesize_figure.clear()
        ax = self.filesize_figure.add_subplot(111)
        apply_dark_style(self.filesize_figure, ax)
        
        sizes = self.stats_data.get('file_sizes', [])
        if sizes:
            ax.hist(sizes, bins=30, color=COLORS['accent'], edgecolor=COLORS['bg'], alpha=0.8)
            ax.set_xlabel('File Size (MB)')
            ax.set_ylabel('Count')
            ax.set_title('File Size Distribution', color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.filesize_figure.tight_layout()
        self.filesize_canvas.draw()
    
    def _update_res_time_chart(self):
        """Update resolution over time chart."""
        self.res_time_figure.clear()
        ax = self.res_time_figure.add_subplot(111)
        apply_dark_style(self.res_time_figure, ax)
        
        res_time = self.stats_data.get('resolution_over_time', [])
        if res_time:
            # Group by month and average
            monthly_res = defaultdict(list)
            for dt, mp in res_time:
                monthly_res[dt.strftime('%Y-%m')].append(mp)
            
            months = sorted(monthly_res.keys())
            avgs = [np.mean(monthly_res[m]) for m in months]
            
            ax.plot(range(len(months)), avgs, color=COLORS['warning'], linewidth=2, marker='o', markersize=4)
            
            step = max(1, len(months) // 12)
            ax.set_xticks(range(0, len(months), step))
            ax.set_xticklabels([months[i] for i in range(0, len(months), step)], rotation=45, ha='right')
            
            ax.set_ylabel('Avg Megapixels')
            ax.set_title('Average Resolution Over Time', color=COLORS['text'], fontsize=11, fontweight='bold')
        
        self.res_time_figure.tight_layout()
        self.res_time_canvas.draw()
    
    def _update_fun_stats(self):
        """Update fun stats cards."""
        s = self.stats_data
        
        # Most/Least tagged
        most = s.get('most_tagged', (None, 0))
        least = s.get('least_tagged', (None, 0))
        
        if most[0]:
            self.most_tagged_card.set_value(f"{most[1]} tags\n{Path(most[0]).name[:20]}")
        if least[0]:
            self.least_tagged_card.set_value(f"{least[1]} tags\n{Path(least[0]).name[:20]}")
        
        # First/Latest
        min_d = s.get('min_date')
        max_d = s.get('max_date')
        if min_d:
            self.oldest_card.set_value(min_d.strftime('%Y-%m-%d'))
        if max_d:
            self.newest_card.set_value(max_d.strftime('%Y-%m-%d'))
        
        # Largest/Smallest
        largest = s.get('largest_file', (None, 0))
        smallest = s.get('smallest_file', (None, 0))
        
        if largest[0]:
            size_mb = largest[1] / (1024 * 1024)
            self.largest_card.set_value(f"{size_mb:.1f} MB\n{Path(largest[0]).name[:20]}")
        if smallest[0]:
            size_kb = smallest[1] / 1024
            self.smallest_card.set_value(f"{size_kb:.0f} KB\n{Path(smallest[0]).name[:20]}")
        
        # Diversity
        self.diversity_card.set_value(f"{s.get('diversity_score', 0):.1f}/100")
    
    def _update_cooccurrence_chart(self):
        """Update tag co-occurrence bar chart."""
        self.cooccurrence_figure.clear()
        ax = self.cooccurrence_figure.add_subplot(111)
        apply_dark_style(self.cooccurrence_figure, ax)
        
        cooccurrence = self.stats_data.get('cooccurrence', [])
        if cooccurrence:
            # Take top 15
            top = cooccurrence[:15]
            labels = [f"{t[0][0][:15]} + {t[0][1][:15]}" for t in reversed(top)]
            counts = [t[1] for t in reversed(top)]
            
            colors = plt.cm.magma(np.linspace(0.3, 0.8, len(labels)))
            ax.barh(labels, counts, color=colors, height=0.7)
            ax.set_xlabel('Co-occurrences')
            ax.set_title('Top Tag Pairs (Co-occurrence)', color=COLORS['text'], fontsize=11, fontweight='bold')
        else:
            ax.text(0.5, 0.5, 'Not enough data', ha='center', va='center', color=COLORS['text_dim'])
        
        self.cooccurrence_figure.tight_layout()
        self.cooccurrence_canvas.draw()
