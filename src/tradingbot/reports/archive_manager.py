"""
Archive Manager - Daily record keeping for trading outputs

Saves all daily outputs with timestamps and creates an index for historical review.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ArchiveManager:
    """Manage daily archival of trading outputs."""
    
    def __init__(self, root: Path) -> None:
        self.root = root
        self.outputs_dir = root / "outputs"
        self.archive_dir = self.outputs_dir / "archive"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
    
    def get_today_archive_dir(self) -> Path:
        """Get or create today's archive directory."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        today_dir = self.archive_dir / today
        today_dir.mkdir(parents=True, exist_ok=True)
        return today_dir
    
    def archive_daily_run(self, run_type: str) -> None:
        """
        Archive outputs from a daily run (morning, midday, close, etc).
        
        Args:
            run_type: "morning", "midday", "close", "news", or "day"
        """
        today_archive = self.get_today_archive_dir()
        timestamp = datetime.utcnow().strftime("%H%M%S")  # HHMMSS
        
        if run_type == "news":
            # Archive catalyst scores
            src = self.outputs_dir / "catalyst_scores.json"
            if src.exists():
                dst = today_archive / f"catalyst_scores_{timestamp}.json"
                shutil.copy2(src, dst)
                logger.info(f"Archived: {dst}")

            social_src = self.outputs_dir / "social_proxy_signals_news.json"
            if social_src.exists():
                social_dst = today_archive / f"social_proxy_signals_news_{timestamp}.json"
                shutil.copy2(social_src, social_dst)
                logger.info(f"Archived: {social_dst}")
        
        elif run_type in ["morning", "midday", "close"]:
            # Archive watchlist, playbook, and smart money signals
            for suffix in ["watchlist.csv", "playbook.md"]:
                src = self.outputs_dir / f"{run_type}_{suffix}"
                if src.exists():
                    dst = today_archive / f"{run_type}_{suffix.replace('.', '_')}_{timestamp}.{suffix.split('.')[-1]}"
                    shutil.copy2(src, dst)
                    logger.info(f"Archived: {dst}")
            
            # Archive smart money signals if they exist
            smart_money_src = self.outputs_dir / f"smart_money_signals_{run_type}.json"
            if smart_money_src.exists():
                dst = today_archive / f"smart_money_signals_{run_type}_{timestamp}.json"
                shutil.copy2(smart_money_src, dst)
                logger.info(f"Archived: {dst}")
        
        elif run_type == "day":
            # Archive the full daily playbook
            src = self.outputs_dir / "daily_playbook.md"
            if src.exists():
                dst = today_archive / f"daily_playbook_{timestamp}.md"
                shutil.copy2(src, dst)
                logger.info(f"Archived: {dst}")

            social_src = self.outputs_dir / "social_proxy_signals_news.json"
            if social_src.exists():
                social_dst = today_archive / f"social_proxy_signals_news_{timestamp}.json"
                shutil.copy2(social_src, social_dst)
                logger.info(f"Archived: {social_dst}")
            
            # Archive all smart money signals from morning and midday
            for session in ["morning", "midday"]:
                smart_money_src = self.outputs_dir / f"smart_money_signals_{session}.json"
                if smart_money_src.exists():
                    dst = today_archive / f"smart_money_signals_{session}_{timestamp}.json"
                    shutil.copy2(smart_money_src, dst)
                    logger.info(f"Archived: {dst}")
    
    def create_daily_index(self) -> None:
        """Create an index of all archived runs for easy browsing."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        today_archive = self.get_today_archive_dir()
        
        # List all archived files
        archived_files = sorted(today_archive.glob("*"))
        
        if not archived_files:
            return
        
        # Create index markdown
        index_lines = [
            f"# Daily Trading Archive - {today}",
            "",
            "## Archived Runs",
            "",
        ]
        
        # Group by run type
        runs_by_type = {}
        for f in archived_files:
            # Extract run type from filename
            name = f.stem
            if name.startswith("catalyst_scores"):
                run_type = "NEWS RESEARCH"
            elif name.startswith("social_proxy_signals"):
                run_type = "SOCIAL PROXY SIGNALS"
            elif name.startswith("smart_money_signals"):
                run_type = "SMART MONEY TRACKING"
            elif name.startswith("morning"):
                run_type = "MORNING PRE-MARKET (8:45 AM)"
            elif name.startswith("midday"):
                run_type = "MIDDAY SCAN (12:00 PM)"
            elif name.startswith("close"):
                run_type = "CLOSE SCAN (3:50 PM)"
            elif name.startswith("daily_playbook"):
                run_type = "DAILY FULL PLAYBOOK"
            else:
                run_type = "OTHER"
            
            if run_type not in runs_by_type:
                runs_by_type[run_type] = []
            runs_by_type[run_type].append(f)
        
        # Write index
        for run_type, files in sorted(runs_by_type.items()):
            index_lines.append(f"### {run_type}")
            index_lines.append("")
            for f in sorted(files, reverse=True):
                # Extract time from filename
                parts = f.stem.split("_")
                time_str = parts[-1] if parts[-1].isdigit() else "?"
                if len(time_str) == 6:
                    time_str = f"{time_str[0:2]}:{time_str[2:4]}:{time_str[4:6]}"
                index_lines.append(f"- **{time_str}** - [{f.name}](./{f.name})")
            index_lines.append("")
        
        # Write index file
        index_path = today_archive / "INDEX.md"
        index_path.write_text("\n".join(index_lines), encoding="utf-8")
        logger.info(f"Created index: {index_path}")
    
    def list_all_archives(self) -> dict[str, list[Path]]:
        """List all archived dates and their files."""
        archives_by_date = {}
        
        if not self.archive_dir.exists():
            return archives_by_date
        
        for date_dir in sorted(self.archive_dir.iterdir(), reverse=True):
            if date_dir.is_dir():
                files = sorted(date_dir.glob("*"), reverse=True)
                # Filter out INDEX files
                files = [f for f in files if not f.name.startswith("INDEX")]
                archives_by_date[date_dir.name] = files
        
        return archives_by_date
    
    def get_archive_summary(self, max_days: int = 7) -> str:
        """Get a summary of recent archives for display."""
        archives = self.list_all_archives()
        
        lines = [
            "# Trading Records - Last 7 Days",
            "",
        ]
        
        for i, (date, files) in enumerate(list(archives.items())[:max_days]):
            lines.append(f"## {date}")
            
            if not files:
                lines.append("No records")
                lines.append("")
                continue
            
            # Count by type
            news_count = len([f for f in files if "catalyst" in f.name])
            social_count = len([f for f in files if "social_proxy" in f.name])
            smart_money_count = len([f for f in files if "smart_money" in f.name])
            morning_count = len([f for f in files if "morning" in f.name and "smart_money" not in f.name])
            midday_count = len([f for f in files if "midday" in f.name and "smart_money" not in f.name])
            close_count = len([f for f in files if "close" in f.name and "smart_money" not in f.name])
            
            if news_count:
                lines.append(f"- News research runs: {news_count}")
            if social_count:
                lines.append(f"- Social proxy snapshots: {social_count}")
            if smart_money_count:
                lines.append(f"- Smart money tracking: {smart_money_count}")
            if morning_count:
                lines.append(f"- Morning scans: {morning_count // 2}")  # Div by 2 for CSV+MD pairs
            if midday_count:
                lines.append(f"- Midday scans: {midday_count // 2}")
            if close_count:
                lines.append(f"- Close scans: {close_count // 2}")
            
            lines.append("")
        
        return "\n".join(lines)
