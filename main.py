#!/usr/bin/env python3
# main.py
"""
SMS Agent — reads recent iMessages, classifies them, and takes action.

Usage:
    python main.py               # run once
    python main.py --dry-run     # preview actions without executing
    python main.py --loop 60     # run every 60 seconds
    python main.py --limit 100   # process last 100 messages
    python main.py --lookback 120  # look back 120 minutes
"""

import argparse
import logging
import time
import sys

import config
import reader
import classifier
import rules
import actions

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE),
    ]
)
logger = logging.getLogger("sms_agent")


def process_once(limit: int, lookback: int):
    """Single pass: read → classify → evaluate rules → act."""

    logger.info(f"--- SMS Agent run | lookback={lookback}m | limit={limit} | dry_run={config.DRY_RUN} ---")

    # 1. Load blocklist — skip already-blocked senders
    blocklist = actions.load_blocklist()

    # 2. Fetch recent inbound messages
    try:
        messages = reader.get_recent_messages(limit=limit, lookback_minutes=lookback)
    except FileNotFoundError as e:
        logger.error(str(e))
        return
    except Exception as e:
        logger.error(f"Failed to read chat.db: {e}")
        return

    if not messages:
        logger.info("No new messages found.")
        return

    logger.info(f"Found {len(messages)} message(s) to process.")

    stats = {"total": len(messages), "actioned": 0, "skipped": 0, "errors": 0}

    for msg in messages:
        sender_id = msg.sender or msg.chat_identifier

        # Skip already-blocked senders
        if sender_id in blocklist:
            logger.info(f"[SKIP] {sender_id} is already blocked.")
            stats["skipped"] += 1
            continue

        logger.info(f"Processing: {msg.display()}")

        # 3. Classify
        try:
            attrs, reason = classifier.classify_message(msg.text)
            msg.attributes = attrs
            logger.info(f"  → Attributes: {attrs} | Reason: {reason}")
        except Exception as e:
            logger.error(f"  → Classification failed: {e}")
            stats["errors"] += 1
            continue

        # 4. Evaluate rules → get actions
        action_list, matched_rule_names = rules.evaluate_detailed(msg)
        if config.DRY_RUN:
            if matched_rule_names:
                logger.info(f"  → Matched rules: {matched_rule_names}")
            else:
                logger.info("  → Matched rules: (none — default log_only)")
        logger.info(f"  → Actions: {action_list}")

        if not action_list or action_list == ["log_only"]:
            stats["skipped"] += 1
            continue

        # 5. Execute actions
        results = actions.execute_actions(msg, action_list)
        if any(results.values()):
            stats["actioned"] += 1
        else:
            stats["errors"] += 1

        logger.info(f"  → Done: {msg.actions_taken}")

    logger.info(
        f"--- Run complete | total={stats['total']} actioned={stats['actioned']} "
        f"skipped={stats['skipped']} errors={stats['errors']} ---\n"
    )


def main():
    parser = argparse.ArgumentParser(description="SMS Agent for iMessage")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions only")
    parser.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                        help="Run continuously every N seconds (0 = run once)")
    parser.add_argument("--limit", type=int, default=config.MESSAGE_FETCH_LIMIT,
                        help="Max messages to process per run")
    parser.add_argument("--lookback", type=int, default=config.LOOKBACK_MINUTES,
                        help="Minutes of history to look back")
    args = parser.parse_args()

    if args.dry_run:
        config.DRY_RUN = True
        logger.info("DRY RUN MODE — no messages will be sent, blocked, or deleted.")

    if args.loop > 0:
        logger.info(f"Running in loop mode every {args.loop}s. Ctrl+C to stop.")
        while True:
            try:
                process_once(args.limit, args.lookback)
                time.sleep(args.loop)
            except KeyboardInterrupt:
                logger.info("Stopped.")
                break
    else:
        process_once(args.limit, args.lookback)


if __name__ == "__main__":  # pragma: no cover
    main()
