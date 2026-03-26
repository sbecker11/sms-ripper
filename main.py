#!/usr/bin/env python3
# main.py
"""
SMS Agent — reads recent iMessages, classifies them, and takes action.

Usage:
    python main.py               # run once (default policy: political)
    python main.py --dry-run     # preview actions without executing
    python main.py --policy spam   # second pass: SPAM / SCAM (after political)
    python main.py --loop 60       # run every 60 seconds
    python main.py --limit 100     # process last 100 messages
    python main.py --lookback 120  # look back 120 minutes
    python main.py --quiet   # minimal progress lines only (no sender/body/reason in logs)
    python main.py --mark-read-phase2   # phase 2: mark each handled row read (Dock badge)
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
from reader import Message

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


def process_once(
    limit: int,
    lookback: int,
    policy: str = "political",
    *,
    quiet: bool = False,
    mark_read_phase2: bool = False,
):
    """Single pass: read → classify → evaluate rules → act."""

    logger.info(
        f"--- SMS Agent run | policy={policy} | lookback={lookback}m | limit={limit} | "
        f"dry_run={config.DRY_RUN} | quiet={quiet} | "
        f"mark_read_phase2={mark_read_phase2} ---"
    )

    _prev_quiet = getattr(config, "QUIET", False)
    config.QUIET = quiet
    try:
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
        pending: list[tuple[Message, list[str], int]] = []
        n_messages = len(messages)

        for i, msg in enumerate(messages, start=1):
            if not quiet:
                logger.info(f"processing {policy.upper()} message {i} of {n_messages}")
            sender_id = msg.sender or msg.chat_identifier

            # Skip already-blocked senders
            if sender_id in blocklist:
                if quiet:
                    logger.info(f"[quiet] {i}/{n_messages} · blocked")
                else:
                    logger.info(f"[SKIP] {sender_id} is already blocked.")
                stats["skipped"] += 1
                continue

            if not quiet:
                logger.info(f"Processing: {msg.display()}")

            # 3. Classify
            try:
                attrs, reason = classifier.classify_message(msg.text)
                msg.attributes = attrs
                if not quiet:
                    logger.info(f"  → Attributes: {attrs} | Reason: {reason}")
            except Exception as e:
                if quiet:
                    logger.error(f"[quiet] {i}/{n_messages} · classify failed: {e}")
                else:
                    logger.error(f"  → Classification failed: {e}")
                stats["errors"] += 1
                continue

            # 4. Evaluate rules → get actions
            action_list, matched_rule_names = rules.evaluate_detailed(msg, policy=policy)
            if config.DRY_RUN and not quiet:
                if matched_rule_names:
                    logger.info(f"  → Matched rules: {matched_rule_names}")
                else:
                    logger.info("  → Matched rules: (none — default log_only)")
            if not quiet:
                logger.info(f"  → Actions: {action_list}")

            if not action_list or action_list == ["log_only"]:
                if quiet:
                    logger.info(f"[quiet] {i}/{n_messages} · skipped")
                stats["skipped"] += 1
                continue

            pending.append((msg, action_list, i))

        stop_queued = sum(
            1
            for _, al, _ in pending
            if "send_stop" in actions._execution_action_order(al)
        )
        archive_queued = sum(
            1
            for _, al, _ in pending
            if actions.action_list_needs_sqlite_archive(al)
        )
        if pending and (archive_queued or stop_queued):
            logger.info(
                f"Two-stage plan: phase 1 = archive for {archive_queued} message(s) while Messages is "
                f"quit; STOP and other UI actions deferred — {stop_queued} message(s) queued for STOP."
            )

        # One quit check before any chat.db archives in this run.
        batch_sqlite_ok: bool | None = None
        if not config.DRY_RUN and pending and any(
            actions.action_list_needs_sqlite_archive(al) for _, al, _ in pending
        ):
            batch_sqlite_ok = actions.messages_quit_guard()

        # Phase 1: all archives while Messages is quit (avoids DB locks / missing trigger UDFs).
        if pending and not config.DRY_RUN and any(
            actions.action_list_needs_sqlite_archive(al) for _, al, _ in pending
        ):
            logger.info("--- Phase 1: archive (Messages should be quit) — STOP not sent yet ---")

        phase1_results: list[dict[str, bool]] = []
        for msg, action_list, _orig_i in pending:
            phase1_results.append(
                actions.execute_actions(
                    msg,
                    action_list,
                    batch_sqlite_ok=batch_sqlite_ok,
                    phases="archive_only",
                )
            )

        archive_failed_for_needed = any(
            actions.action_list_needs_sqlite_archive(al)
            and not phase1_results[i].get("archive", False)
            for i, (_, al, _) in enumerate(pending)
        )
        if archive_failed_for_needed:
            logger.warning(
                "[ARCHIVE] Phase 1 failed for at least one row that needed archive — "
                "the live message may still be in Messages. Phase 2 still runs (STOP/block/etc.). "
                "If you see 'no such function: before_delete_attachment_path' or "
                "'delete_attachment_path', update sms-ripper (SQL trigger stubs) and re-run with Messages quit for phase 1."
            )

        any_ui = any(
            any(
                a not in actions.DIRECT_SQLITE_ARCHIVE_ACTIONS
                for a in actions._execution_action_order(al)
            )
            for _, al, _ in pending
        )
        if pending and any_ui:
            logger.info(
                "--- Phase 2: send_stop / block / delete / log_only (Messages opens here) ---"
            )
            if mark_read_phase2:
                logger.info(
                    "[MARK_READ] After each message, chat.db read flags update (Dock badge may drop live)."
                )
            need_activate = mark_read_phase2 or any(
                actions.action_list_needs_messages_activate(al) for _, al, _ in pending
            )
            if need_activate:
                if stop_queued:
                    if archive_failed_for_needed:
                        logger.info(
                            f"Sending {stop_queued} queued STOP reply(ies) now "
                            f"(phase 1 archive did not complete for all rows — see [ARCHIVE] above)."
                        )
                    else:
                        logger.info(
                            f"Sending {stop_queued} queued STOP reply(ies) now "
                            f"(phase 1 archives complete)."
                        )
                elif any(
                    actions.action_list_needs_messages_activate(al) for _, al, _ in pending
                ):
                    logger.info("Phase 2 needs Messages for delete (or similar); activating.")
                else:
                    logger.info("Activating Messages for live read-state / badge updates.")
                actions.activate_messages()

        for i, (msg, action_list, orig_i) in enumerate(pending):
            r_ui = actions.execute_actions(msg, action_list, phases="ui_only")
            results = {**phase1_results[i], **r_ui}
            if any(results.values()):
                stats["actioned"] += 1
            else:
                stats["errors"] += 1

            if quiet:
                ok = any(results.values())
                logger.info(
                    f"[quiet] {orig_i}/{n_messages} · {'ok' if ok else 'failed'}"
                )
            else:
                logger.info(f"  → Done: {msg.actions_taken}")
            if mark_read_phase2:
                actions.mark_inbound_read(msg)

        logger.info(
            f"--- Run complete | total={stats['total']} actioned={stats['actioned']} "
            f"skipped={stats['skipped']} errors={stats['errors']} ---\n"
        )

    finally:
        config.QUIET = _prev_quiet


def main():
    parser = argparse.ArgumentParser(description="SMS Agent for iMessage")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions only")
    parser.add_argument("--loop", type=int, default=0, metavar="SECONDS",
                        help="Run continuously every N seconds (0 = run once)")
    parser.add_argument("--limit", type=int, default=config.MESSAGE_FETCH_LIMIT,
                        help="Max messages to process per run")
    parser.add_argument("--lookback", type=int, default=config.LOOKBACK_MINUTES,
                        help="Minutes of history to look back")
    parser.add_argument(
        "--policy",
        choices=["political", "spam"],
        default="political",
        help="Rule set: political (archive/STOP/block for POLITICAL) or spam (STOP/block/delete for SPAM/SCAM). "
        "Run political first, then spam in a separate pass.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Minimal per-message progress in logs (no sender/body/classifier detail).",
    )
    parser.add_argument(
        "--mark-read-phase2",
        action="store_true",
        help="After phase-2 UI actions, set is_read on each pending inbound row in chat.db "
        "(Messages should be open; may lower the Dock unread count as you go).",
    )
    args = parser.parse_args()

    if args.dry_run:
        config.DRY_RUN = True
        logger.info("DRY RUN MODE — no messages will be sent, blocked, or deleted.")

    if args.loop > 0:
        logger.info(f"Running in loop mode every {args.loop}s. Ctrl+C to stop.")
        while True:
            try:
                process_once(
                    args.limit,
                    args.lookback,
                    args.policy,
                    quiet=args.quiet,
                    mark_read_phase2=args.mark_read_phase2,
                )
                time.sleep(args.loop)
            except KeyboardInterrupt:
                logger.info("Stopped.")
                break
    else:
        process_once(
            args.limit,
            args.lookback,
            args.policy,
            quiet=args.quiet,
            mark_read_phase2=args.mark_read_phase2,
        )


if __name__ == "__main__":  # pragma: no cover
    main()
