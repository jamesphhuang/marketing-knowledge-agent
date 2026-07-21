from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .apply_review_decisions import ApplyReviewDecisionsError, apply_review_decisions
from .asset_apply_preview import (
    AssetApplyPreviewError,
    generate_asset_apply_preview,
)
from .asset_apply_plan import (
    AssetApplyPlanError,
    create_asset_metadata_apply_plan,
    reject_unimplemented_apply_stage,
)
from .asset_metadata_preview import (
    AssetMetadataPreviewError,
    generate_asset_metadata_preview,
)
from .asset_review_validation import (
    AssetReviewValidationError,
    validate_asset_review_decisions,
)
from .backfill import generate_backfill_report
from .content_index import (
    DEFAULT_CONTENT_INDEX_DB,
    DEFAULT_CONTENT_INDEX_REPORT_DIR,
    ContentIndexError,
    build_content_index,
)
from .evaluation import evaluate
from .excel_preview import ExcelPreviewError, generate_excel_preview
from .ingestion import IngestionError
from .llm import LLMError
from .models import SearchFilters
from .missing_parent_diagnostic import (
    MissingParentDiagnosticError,
    diagnose_missing_formal_parents,
)
from .missing_parent_resolution_preview import (
    MissingParentResolutionPreviewError,
    generate_missing_parent_resolution_preview,
)
from .missing_parent_resolution_apply_preview import (
    MissingParentResolutionApplyPreviewError,
    generate_resolution_apply_preview,
)
from .resolution_storage_schema_preview import (
    ResolutionStorageSchemaError,
    generate_resolution_storage_schema_preview,
)
from .governance_decision_store_plan import (
    GovernanceDecisionStorePlanError,
    generate_governance_decision_store_plan,
)
from .governance_decision_store_regenerated_plan import (
    DEFAULT_BUNDLE_PATH as DEFAULT_PARENT_AUTHORITY_IMPORT_BUNDLE,
    DEFAULT_OUTPUT_DIR as DEFAULT_REGENERATED_DECISION_STORE_PLAN_OUTPUT,
    DEFAULT_TARGET_PATH as DEFAULT_GOVERNANCE_DECISION_STORE_TARGET,
    RegeneratedDecisionStorePlanError,
    generate_regenerated_governance_decision_store_plan,
)
from .governance_decision_store_confirmation import (
    DEFAULT_BUNDLE_PATH as DEFAULT_CONFIRMATION_BUNDLE_PATH,
    DEFAULT_CONFIRMATION_PATH as DEFAULT_DECISION_STORE_CONFIRMATION_PATH,
    DEFAULT_FORMAL_TARGET as DEFAULT_CONFIRMATION_FORMAL_TARGET,
    DEFAULT_PLAN_MANIFEST as DEFAULT_CONFIRMATION_PLAN_MANIFEST,
    DEFAULT_REPORT_DIR as DEFAULT_DECISION_STORE_CONFIRMATION_REPORTS,
    GovernanceDecisionStoreConfirmationError,
    confirm_governance_decision_store_plan,
    validate_governance_decision_store_plan,
)
from .governance_decision_store_execution import (
    DEFAULT_EXECUTION_BUNDLE as DEFAULT_DECISION_STORE_EXECUTION_BUNDLE,
    DEFAULT_FORMAL_TARGET as DEFAULT_DECISION_STORE_EXECUTION_TARGET,
    DEFAULT_REPORT_DIR as DEFAULT_DECISION_STORE_EXECUTION_REPORTS,
    GovernanceDecisionStoreExecutionError,
    execute_governance_decision_store_plan,
)
from .governance_decision_store_schema_v2_plan import (
    DEFAULT_BUNDLE_PATH as DEFAULT_SCHEMA_V2_BUNDLE_PATH,
    DEFAULT_EXECUTE_REPORTS as DEFAULT_SCHEMA_V2_EXECUTE_REPORTS,
    DEFAULT_OLD_CONFIRMATION as DEFAULT_SCHEMA_V2_OLD_CONFIRMATION,
    DEFAULT_OLD_PLAN_MANIFEST as DEFAULT_SCHEMA_V2_OLD_PLAN_MANIFEST,
    DEFAULT_OUTPUT_DIR as DEFAULT_SCHEMA_V2_OUTPUT,
    DEFAULT_TARGET_PATH as DEFAULT_SCHEMA_V2_TARGET,
    GovernanceDecisionStoreSchemaV2PlanError,
    generate_governance_decision_store_schema_v2_plan,
)
from .governance_decision_store_schema_v2_confirmation import (
    DEFAULT_BUNDLE_PATH as DEFAULT_SCHEMA_V2_CONFIRMATION_BUNDLE,
    DEFAULT_CANONICAL_SCHEMA as DEFAULT_SCHEMA_V2_CONFIRMATION_SCHEMA,
    DEFAULT_CONFIRMATION_PATH as DEFAULT_SCHEMA_V2_CONFIRMATION_PATH,
    DEFAULT_FORMAL_TARGET as DEFAULT_SCHEMA_V2_CONFIRMATION_TARGET,
    DEFAULT_OLD_CONFIRMATION_PATH as DEFAULT_SCHEMA_V2_HISTORICAL_CONFIRMATION,
    DEFAULT_PLAN_MANIFEST as DEFAULT_SCHEMA_V2_CONFIRMATION_PLAN,
    DEFAULT_REPORT_DIR as DEFAULT_SCHEMA_V2_CONFIRMATION_REPORTS,
    DEFAULT_SCHEMA_HASH as DEFAULT_SCHEMA_V2_CONFIRMATION_HASH_FILE,
    GovernanceDecisionStoreSchemaV2ConfirmationError,
    confirm_governance_decision_store_schema_v2_plan,
    validate_governance_decision_store_schema_v2_plan,
)
from .governance_decision_store_schema_v2_execution import (
    DEFAULT_EXECUTION_BUNDLE as DEFAULT_SCHEMA_V2_EXECUTION_BUNDLE,
    DEFAULT_FORMAL_TARGET as DEFAULT_SCHEMA_V2_EXECUTION_TARGET,
    DEFAULT_REPORT_DIR as DEFAULT_SCHEMA_V2_EXECUTION_REPORTS,
    GovernanceDecisionStoreSchemaV2ExecutionError,
    execute_governance_decision_store_schema_v2_plan,
)
from .governance_decision_store_existing_validation import (
    DEFAULT_FORMAL_TARGET as DEFAULT_EXISTING_DECISION_STORE_TARGET,
    DEFAULT_REPORT_DIR as DEFAULT_EXISTING_DECISION_STORE_REPORTS,
    ExistingGovernanceDecisionStoreValidationError,
    validate_existing_governance_decision_store,
)
from .parent_sync_plan import (
    DEFAULT_DECISION_STORE as DEFAULT_PARENT_SYNC_DECISION_STORE,
    DEFAULT_OUTPUT_DIR as DEFAULT_PARENT_SYNC_OUTPUT,
    ParentSyncPlanError,
    generate_parent_sync_plan,
)
from .parent_sync_confirmation import (
    DEFAULT_CONFIRMATION_PATH as DEFAULT_PARENT_SYNC_CONFIRMATION_PATH,
    DEFAULT_REPORT_DIR as DEFAULT_PARENT_SYNC_CONFIRMATION_REPORTS,
    ParentSyncConfirmationError,
    confirm_parent_sync_plan,
    validate_parent_sync_plan,
)
from .parent_authority_review import (
    ParentAuthorityReviewError,
    prepare_parent_authority_review,
)
from .parent_authority_import_bundle import (
    DEFAULT_BUNDLE_PATH as DEFAULT_PARENT_AUTHORITY_BUNDLE_PATH,
    DEFAULT_REPORT_DIR as DEFAULT_PARENT_AUTHORITY_BUNDLE_REPORT_DIR,
    ParentAuthorityImportBundleError,
    create_parent_authority_import_bundle,
    validate_parent_authority_import_bundle,
)
from .pipeline import (
    DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    agent_ask,
    ask_index,
    explain_query,
    ingest_vault,
    resolve_governance_index,
    search_index,
)
from .query_gating import precheck_restricted_query
from .retrieval import result_to_dict
from .review_template import ReviewTemplateError, generate_review_template
from .review_decision_validation import ReviewDecisionValidationError, validate_review_decisions
from .slack_interface import SlackInterfaceError, run_slack_bot
from .slack_output_preview import (
    SAMPLE_QUERIES,
    SlackOutputPreviewError,
    generate_slack_output_preview,
    preview_slack_query,
)
from .obsidian_sync import (
    DEFAULT_NAMESPACE,
    DEFAULT_OBSIDIAN_VAULT,
    DEFAULT_SYNC_OUTPUT,
    ObsidianSyncError,
    create_sync_plan,
    execute_sync_plan,
    rollback_sync,
)
from .validation import validate_vault


DEFAULT_VAULT = Path("data/mock_vault")
DEFAULT_DB = Path(".mka/index.sqlite")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "ingest":
            summary = ingest_vault(vault_path=args.vault, db_path=args.db)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "search":
            filters = _filters_from_args(args)
            governance_index, _ = resolve_governance_index(None, args.restricted_customers)
            refused = precheck_restricted_query(args.query, governance_index, command="search")
            if refused is not None:
                print(refused.answer)
                return 0
            results = search_index(
                args.query,
                db_path=args.db,
                filters=filters,
                limit=args.limit,
                mode=args.mode,
            )
            payload = [result_to_dict(result) for result in results]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "ask":
            filters = _filters_from_args(args)
            answer = ask_index(
                args.question,
                db_path=args.db,
                filters=filters,
                limit=args.limit,
                mode=args.mode,
                restricted_customers_path=args.restricted_customers,
                provider_name=args.provider,
                dry_run_llm=args.dry_run_llm,
            )
            _print_answer(answer)
            return 0

        if args.command == "explain-query":
            filters = _filters_from_args(args)
            governance_index, load_warning = resolve_governance_index(None, args.restricted_customers)
            refused = precheck_restricted_query(args.query, governance_index, command="explain-query")
            if refused is not None:
                print(refused.answer)
                return 0
            payload = explain_query(
                args.query,
                db_path=args.db,
                filters=filters,
                limit=args.limit,
                mode=args.mode,
                governance_index=governance_index,
            )
            if load_warning:
                payload["warnings"] = [load_warning]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "agent-ask":
            filters = _filters_from_args(args)
            answer = agent_ask(
                args.question,
                db_path=args.db,
                filters=filters,
                limit=args.limit,
                mode=args.mode,
                restricted_customers_path=args.restricted_customers,
                provider_name=args.provider,
                dry_run_llm=args.dry_run_llm,
            )
            _print_answer(answer)
            if args.show_trace:
                print("\nAgent Trace:")
                print(json.dumps(answer.trace.to_dict(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate":
            report = validate_vault(vault_path=args.vault)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["summary"]["invalid"] == 0 else 1

        if args.command == "backfill-report":
            report = generate_backfill_report(vault_path=args.vault, output_path=args.output)
            payload = {
                "vault_path": report["vault_path"],
                "output_path": report["output_path"],
                "candidate_count": report["candidate_count"],
                "validation_summary": report["validation_summary"],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if args.command == "evaluate":
            report = evaluate(vault_path=args.vault, db_path=args.db)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0

        if args.command == "excel-preview":
            summary = generate_excel_preview(
                workbook_path=args.workbook,
                output_dir=args.output,
                captured_date=args.captured_date,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "asset-metadata-preview":
            summary = generate_asset_metadata_preview(
                preview_dir=args.preview_dir,
                output_dir=args.output,
                workbook_path=args.workbook,
                vault_path=args.vault,
                db_path=args.db,
                decisions_path=args.decisions,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate-asset-review-decisions":
            summary = validate_asset_review_decisions(
                decisions_path=args.decisions,
                inventory_path=args.inventory,
                enrichment_path=args.enrichment,
                output_dir=args.output,
                restricted_customers_path=args.restricted_customers,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["error_count"] == 0 else 1

        if args.command == "apply-asset-review-decisions":
            summary = generate_asset_apply_preview(
                decisions_path=args.decisions,
                inventory_path=args.inventory,
                enrichment_path=args.enrichment,
                validation_dir=args.validation_dir,
                output_dir=args.output,
                restricted_customers_path=args.restricted_customers,
                vault_path=args.vault,
                db_path=args.db,
                workbook_path=args.workbook,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["error_count"] == 0 else 1

        if args.command == "apply-asset-metadata":
            if args.confirm_plan_id:
                reject_unimplemented_apply_stage("confirm", args.confirm_plan_id)
            if args.execute_plan_id:
                reject_unimplemented_apply_stage("execute", args.execute_plan_id)
            summary = create_asset_metadata_apply_plan(
                apply_preview_path=args.apply_preview,
                blocked_preview_path=args.blocked_preview,
                inventory_path=args.inventory,
                parent_records_path=args.parent_records,
                decisions_path=args.decisions,
                validation_dir=args.validation_dir,
                restricted_customers_path=args.restricted_customers,
                vault_path=args.vault,
                db_path=args.db,
                output_dir=args.output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1 if summary["execution_blocked"] else 0

        if args.command == "diagnose-missing-formal-parents":
            summary = diagnose_missing_formal_parents(
                join_validation_path=args.join_validation,
                apply_preview_path=args.apply_preview,
                parent_records_path=args.parent_records,
                review_decisions_path=args.review_decisions,
                restricted_customers_path=args.restricted_customers,
                vault_path=args.vault,
                db_path=args.db,
                output_dir=args.output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "preview-missing-parent-resolution":
            summary = generate_missing_parent_resolution_preview(
                parent_records_path=args.parent_records,
                review_decisions_path=args.review_decisions,
                inventory_path=args.inventory,
                apply_preview_path=args.apply_preview,
                blocked_preview_path=args.blocked_preview,
                restricted_customers_path=args.restricted_customers,
                vault_path=args.vault,
                db_path=args.db,
                production_slack_renderer_path=args.production_slack_renderer,
                output_dir=args.output,
                reviewed_at=args.reviewed_at,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate-missing-parent-resolution":
            summary = generate_resolution_apply_preview(
                resolution_dir=args.resolution_dir,
                parent_records_path=args.parent_records,
                review_decisions_path=args.review_decisions,
                inventory_path=args.inventory,
                asset_apply_preview_path=args.asset_apply_preview,
                asset_blocked_preview_path=args.asset_blocked_preview,
                restricted_customers_path=args.restricted_customers,
                vault_path=args.vault,
                db_path=args.db,
                production_slack_renderer_path=args.production_slack_renderer,
                output_dir=args.output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "preview-resolution-storage-schema":
            summary = generate_resolution_storage_schema_preview(
                resolution_dir=args.resolution_dir,
                parent_records_path=args.parent_records,
                review_decisions_path=args.review_decisions,
                asset_apply_preview_path=args.asset_apply_preview,
                asset_blocked_preview_path=args.asset_blocked_preview,
                vault_path=args.vault,
                db_path=args.db,
                output_dir=args.output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "governance-decision-store":
            summary = generate_governance_decision_store_plan(
                review_decisions_path=args.review_decisions,
                merchant_cases_path=args.merchant_cases,
                public_metrics_path=args.public_metrics,
                pending_metrics_path=args.pending_metrics,
                restricted_customers_path=args.restricted_customers,
                asset_decisions_path=args.asset_decisions,
                asset_validation_path=args.asset_validation,
                asset_apply_preview_path=args.asset_apply_preview,
                asset_blocked_preview_path=args.asset_blocked_preview,
                resolution_dir=args.resolution_dir,
                formal_vault_path=args.vault,
                formal_db_path=args.db,
                production_renderer_path=args.production_slack_renderer,
                output_dir=args.output,
                created_at=args.created_at,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1 if summary["execution_blocked"] else 0

        if args.command == "regenerate-governance-decision-store-plan":
            summary = generate_regenerated_governance_decision_store_plan(
                repo_root=Path.cwd(),
                bundle_path=args.bundle,
                legacy_decisions_path=args.legacy_decisions,
                merchant_cases_path=args.merchant_cases,
                asset_url_decisions_path=args.asset_url_decisions,
                asset_url_validation_path=args.asset_url_validation,
                asset_apply_preview_path=args.asset_apply_preview,
                asset_blocked_preview_path=args.asset_blocked_preview,
                formal_vault_path=args.vault,
                formal_db_path=args.db,
                production_renderer_path=args.production_slack_renderer,
                target_path=args.target,
                output_dir=args.output,
                created_at=args.created_at,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1 if summary["execution_blocked"] else 0

        if args.command == "validate-governance-decision-store-plan":
            summary = validate_governance_decision_store_plan(
                repo_root=Path.cwd(),
                plan_id=args.plan_id,
                manifest_hash=args.manifest_hash,
                plan_manifest_path=args.plan_manifest,
                bundle_path=args.bundle,
                formal_target_path=args.target,
                now=args.now,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "confirm-governance-decision-store-plan":
            summary = confirm_governance_decision_store_plan(
                repo_root=Path.cwd(),
                plan_id=args.plan_id,
                manifest_hash=args.manifest_hash,
                reviewer=args.reviewer,
                confirmed_at=args.confirmed_at,
                plan_manifest_path=args.plan_manifest,
                bundle_path=args.bundle,
                formal_target_path=args.target,
                confirmation_path=args.confirmation_path,
                report_dir=args.output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "execute-governance-decision-store-plan":
            summary = execute_governance_decision_store_plan(
                repo_root=Path.cwd(),
                plan_id=args.plan_id,
                manifest_hash=args.manifest_hash,
                confirmation_id=args.confirmation_id,
                confirmation_root_hash=args.confirmation_root_hash,
                formal_target_path=args.target,
                execution_bundle_path=args.execution_bundle,
                report_dir=args.output,
                executed_at=args.executed_at,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1 if summary["execution_blocked"] else 0

        if args.command == "plan-governance-decision-store-schema-v2":
            summary = generate_governance_decision_store_schema_v2_plan(
                repo_root=Path.cwd(),
                bundle_path=args.bundle,
                old_plan_manifest_path=args.old_plan_manifest,
                old_confirmation_path=args.old_confirmation,
                execute_reports_path=args.execute_reports,
                legacy_decisions_path=args.legacy_decisions,
                merchant_cases_path=args.merchant_cases,
                asset_url_decisions_path=args.asset_url_decisions,
                asset_url_validation_path=args.asset_url_validation,
                asset_apply_preview_path=args.asset_apply_preview,
                asset_blocked_preview_path=args.asset_blocked_preview,
                formal_vault_path=args.vault,
                formal_db_path=args.db,
                production_renderer_path=args.production_slack_renderer,
                target_path=args.target,
                output_dir=args.output,
                temporary_dir=args.temporary_dir,
                created_at=args.created_at,
            )
            public_summary = {
                key: value for key, value in summary.items() if key != "event_templates"
            }
            print(json.dumps(public_summary, ensure_ascii=False, indent=2))
            return 1 if summary["execution_blocked"] else 0

        if args.command == "validate-governance-decision-store-schema-v2-plan":
            summary = validate_governance_decision_store_schema_v2_plan(
                repo_root=Path.cwd(),
                plan_id=args.plan_id,
                manifest_hash=args.manifest_hash,
                schema_hash=args.schema_hash,
                canonical_sql_hash=args.canonical_sql_hash,
                plan_manifest_path=args.plan_manifest,
                canonical_schema_path=args.canonical_schema,
                schema_hash_path=args.schema_hash_file,
                bundle_path=args.bundle,
                old_confirmation_path=args.old_confirmation,
                formal_target_path=args.target,
                temporary_root=args.temporary_root,
                now=args.now,
            )
            public_summary = {
                key: value for key, value in summary.items()
                if key not in {"event_templates", "plan_manifest", "bundle_checksum_rows", "special_decision_rows"}
            }
            print(json.dumps(public_summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "confirm-governance-decision-store-schema-v2-plan":
            summary = confirm_governance_decision_store_schema_v2_plan(
                repo_root=Path.cwd(),
                plan_id=args.plan_id,
                manifest_hash=args.manifest_hash,
                schema_hash=args.schema_hash,
                canonical_sql_hash=args.canonical_sql_hash,
                reviewer=args.reviewer,
                confirmed_at=args.confirmed_at,
                plan_manifest_path=args.plan_manifest,
                canonical_schema_path=args.canonical_schema,
                schema_hash_path=args.schema_hash_file,
                bundle_path=args.bundle,
                old_confirmation_path=args.old_confirmation,
                formal_target_path=args.target,
                temporary_root=args.temporary_root,
                confirmation_path=args.confirmation_path,
                report_dir=args.output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "execute-governance-decision-store-schema-v2-plan":
            summary = execute_governance_decision_store_schema_v2_plan(
                repo_root=Path.cwd(),
                plan_id=args.plan_id,
                manifest_hash=args.plan_manifest_hash,
                schema_hash=args.schema_hash,
                confirmation_id=args.confirmation_id,
                confirmation_root_hash=args.confirmation_root_hash,
                formal_target_path=args.target,
                execution_bundle_path=args.execution_bundle,
                confirmation_bundle_path=args.confirmation_bundle,
                report_dir=args.output,
                temporary_root=args.temporary_root,
                executed_at=args.executed_at,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate-existing-governance-decision-store":
            summary = validate_existing_governance_decision_store(
                repo_root=Path.cwd(),
                database_path=args.database,
                report_dir=args.output,
                temporary_root=args.temporary_root,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "plan-parent-sync":
            summary = generate_parent_sync_plan(
                repo_root=Path.cwd(),
                decision_store_path=args.decision_store,
                output_dir=args.output,
                temporary_root=args.temporary_root,
                created_at=args.created_at,
            )
            public_summary = {
                key: value for key, value in summary.items()
                if key not in {
                    "authoritative_projection", "reconciliation_rows", "field_diff_rows",
                    "path_plan_rows", "write_manifest_records",
                    "formal_sqlite_projection_rows", "manifest",
                }
            }
            print(json.dumps(public_summary, ensure_ascii=False, indent=2))
            return 1 if summary["execution_blocked"] else 0

        if args.command == "validate-parent-sync-plan":
            summary = validate_parent_sync_plan(
                repo_root=Path.cwd(), plan_id=args.plan_id,
                manifest_hash=args.manifest_hash, temporary_root=args.temporary_root,
                validated_at=args.validated_at,
            )
            public_summary = {
                key: value for key, value in summary.items()
                if key not in {
                    "authoritative_projection", "reconciliation", "field_necessity_rows",
                    "governance_only_rows", "not_projected_rows", "create_rows",
                    "managed_vault_delta", "formal_sqlite_delta", "offline_search",
                    "special_validation", "plan_manifest",
                }
            }
            print(json.dumps(public_summary, ensure_ascii=False, indent=2))
            return 0 if summary["confirmation_allowed"] else 1

        if args.command == "confirm-parent-sync-plan":
            summary = confirm_parent_sync_plan(
                repo_root=Path.cwd(), plan_id=args.plan_id,
                manifest_hash=args.manifest_hash, reviewer=args.reviewer,
                confirmed_at=args.confirmed_at,
                confirmation_path=args.confirmation_path, report_dir=args.output,
                temporary_root=args.temporary_root,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["confirmation_created"] else 1

        if args.command == "prepare-parent-authority-review":
            summary = prepare_parent_authority_review(
                merchant_cases_path=args.merchant_cases,
                review_decisions_path=args.review_decisions,
                admin_resolutions_path=args.admin_resolutions,
                baseline_import_preview_path=args.baseline_import_preview,
                decision_source_inventory_path=args.decision_source_inventory,
                asset_inventory_path=args.asset_inventory,
                asset_resolution_path=args.asset_resolution,
                asset_url_decisions_path=args.asset_url_decisions,
                formal_vault_path=args.vault,
                formal_db_path=args.db,
                decision_store_path=args.decision_store,
                output_dir=args.output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "create-parent-authority-import-bundle":
            summary = create_parent_authority_import_bundle(
                repo_root=Path.cwd(),
                target_path=args.target,
                report_dir=args.output,
                created_at=args.created_at,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate-parent-authority-import-bundle":
            summary = validate_parent_authority_import_bundle(args.bundle)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "preview-slack-output":
            if args.sample_set:
                summary = generate_slack_output_preview(
                    queries=SAMPLE_QUERIES,
                    db_path=args.db,
                    apply_preview_path=args.apply_preview,
                    blocked_preview_path=args.blocked_preview,
                    decisions_path=args.decisions,
                    restricted_customers_path=args.restricted_customers,
                    output_dir=args.output,
                    vault_path=args.vault,
                    workbook_path=args.workbook,
                )
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0 if summary["error_count"] == 0 else 1
            print(
                preview_slack_query(
                    query=args.query,
                    variant=args.variant,
                    db_path=args.db,
                    apply_preview_path=args.apply_preview,
                    blocked_preview_path=args.blocked_preview,
                    decisions_path=args.decisions,
                    restricted_customers_path=args.restricted_customers,
                )
            )
            return 0

        if args.command == "review-template":
            summary = generate_review_template(
                preview_dir=args.preview_dir,
                output_path=args.output,
                summary_output_path=args.summary_output,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "validate-review-decisions":
            summary = validate_review_decisions(
                decisions_path=args.decisions,
                output_path=args.output,
                preview_dir=args.preview_dir,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0 if summary["error_count"] == 0 else 1

        if args.command == "apply-review-decisions":
            summary = apply_review_decisions(
                decisions_path=args.decisions,
                preview_dir=args.preview_dir,
                output_dir=args.output,
                include_clean_records=args.include_clean_records,
                include_clean_merchant_cases=args.include_clean_merchant_cases,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if args.command == "sync-obsidian":
            if args.sync_action == "plan":
                plan = create_sync_plan(
                    apply_dir=args.apply_dir,
                    vault_path=args.vault,
                    namespace=args.namespace,
                    output_dir=DEFAULT_SYNC_OUTPUT,
                )
                _print_sync_summary(plan)
                return 1 if _plan_has_conflicts(plan) else 0
            if args.sync_action == "execute":
                result = execute_sync_plan(
                    plan_path=args.plan,
                    vault_path=args.vault,
                    confirm=args.confirm,
                    allow_conflicts_skip=args.allow_conflicts_skip,
                )
                _print_sync_summary(result)
                return 1 if result.get("requires_confirmation") else 0
            if args.sync_action == "rollback":
                result = rollback_sync(batch_id=args.batch, vault_path=args.vault, output_dir=DEFAULT_SYNC_OUTPUT)
                _print_sync_summary(result)
                return 0

        if args.command == "build-content-index":
            summary = build_content_index(
                vault_path=args.vault,
                namespace=args.namespace,
                db_path=args.db,
                report_dir=args.report_dir,
                confirm=args.confirm,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return summary["exit_code"]

        if args.command == "slack-bot":
            run_slack_bot(config_path=args.config)
            return 0

    except IngestionError as exc:
        print(f"ingestion error: {exc}", file=sys.stderr)
        return 2
    except ExcelPreviewError as exc:
        print(f"excel preview error: {exc}", file=sys.stderr)
        return 2
    except AssetMetadataPreviewError as exc:
        print(f"asset metadata preview error: {exc}", file=sys.stderr)
        return 2
    except AssetApplyPreviewError as exc:
        print(f"asset apply preview error: {exc}", file=sys.stderr)
        return 2
    except AssetApplyPlanError as exc:
        print(f"asset apply plan error: {exc}", file=sys.stderr)
        return 2
    except MissingParentDiagnosticError as exc:
        print(f"missing parent diagnostic error: {exc}", file=sys.stderr)
        return 2
    except MissingParentResolutionPreviewError as exc:
        print(f"missing parent resolution preview error: {exc}", file=sys.stderr)
        return 2
    except MissingParentResolutionApplyPreviewError as exc:
        print(f"missing parent resolution validation error: {exc}", file=sys.stderr)
        return 2
    except ResolutionStorageSchemaError as exc:
        print(f"resolution storage schema preview error: {exc}", file=sys.stderr)
        return 2
    except GovernanceDecisionStorePlanError as exc:
        print(f"governance decision store plan error: {exc}", file=sys.stderr)
        return 2
    except RegeneratedDecisionStorePlanError as exc:
        print(f"regenerated decision store plan error: {exc}", file=sys.stderr)
        return 2
    except GovernanceDecisionStoreConfirmationError as exc:
        print(f"governance decision store confirmation error: {exc}", file=sys.stderr)
        return 2
    except GovernanceDecisionStoreExecutionError as exc:
        print(f"governance decision store execution error: {exc}", file=sys.stderr)
        return 2
    except GovernanceDecisionStoreSchemaV2PlanError as exc:
        print(f"governance decision store schema v2 plan error: {exc}", file=sys.stderr)
        return 2
    except GovernanceDecisionStoreSchemaV2ConfirmationError as exc:
        print(f"governance decision store schema v2 confirmation error: {exc}", file=sys.stderr)
        return 2
    except GovernanceDecisionStoreSchemaV2ExecutionError as exc:
        print(f"governance decision store schema v2 execution error: {exc}", file=sys.stderr)
        return 2
    except ExistingGovernanceDecisionStoreValidationError as exc:
        print(f"existing governance decision store validation error: {exc}", file=sys.stderr)
        return 2
    except ParentSyncPlanError as exc:
        print(f"parent sync plan error: {exc}", file=sys.stderr)
        return 2
    except ParentSyncConfirmationError as exc:
        print(f"parent sync confirmation error: {exc}", file=sys.stderr)
        return 2
    except ParentAuthorityReviewError as exc:
        print(f"parent authority review error: {exc}", file=sys.stderr)
        return 2
    except ParentAuthorityImportBundleError as exc:
        print(f"parent authority import bundle error: {exc}", file=sys.stderr)
        return 2
    except SlackOutputPreviewError as exc:
        print(f"slack output preview error: {exc}", file=sys.stderr)
        return 2
    except AssetReviewValidationError as exc:
        print(f"asset review validation error: {exc}", file=sys.stderr)
        return 2
    except ReviewTemplateError as exc:
        print(f"review template error: {exc}", file=sys.stderr)
        return 2
    except ReviewDecisionValidationError as exc:
        print(f"review decision validation error: {exc}", file=sys.stderr)
        return 2
    except ApplyReviewDecisionsError as exc:
        print(f"apply review decisions error: {exc}", file=sys.stderr)
        return 2
    except ObsidianSyncError as exc:
        print(f"obsidian sync error: {exc}", file=sys.stderr)
        return 2
    except ContentIndexError as exc:
        print(f"content index error: {exc}", file=sys.stderr)
        return 2
    except LLMError as exc:
        print(f"llm error: {exc}", file=sys.stderr)
        return 2
    except SlackInterfaceError as exc:
        print(f"slack interface error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"file error: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mka", description="Marketing Knowledge Agent prototype")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Build a SQLite index from a Markdown vault")
    ingest_parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    ingest_parser.add_argument("--db", type=Path, default=DEFAULT_DB)

    validate_parser = subparsers.add_parser("validate", help="Validate Markdown vault metadata")
    validate_parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)

    backfill_parser = subparsers.add_parser(
        "backfill-report",
        help="Generate review-only metadata backfill candidates",
    )
    backfill_parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    backfill_parser.add_argument("--output", type=Path, default=Path("reports/metadata_backfill_candidates.md"))

    search_parser = subparsers.add_parser("search", help="Search indexed marketing knowledge")
    search_parser.add_argument("query")
    _add_retrieval_args(search_parser)
    _add_governance_args(search_parser)

    ask_parser = subparsers.add_parser("ask", help="Generate a mock RAG answer with citations")
    ask_parser.add_argument("question")
    _add_retrieval_args(ask_parser)
    _add_governance_args(ask_parser)
    _add_llm_args(ask_parser)

    agent_ask_parser = subparsers.add_parser(
        "agent-ask",
        help="Run an offline agentic-lite RAG answer with citations and optional trace",
    )
    agent_ask_parser.add_argument("question")
    _add_retrieval_args(agent_ask_parser)
    _add_governance_args(agent_ask_parser)
    _add_llm_args(agent_ask_parser)
    agent_ask_parser.add_argument("--show-trace", action="store_true")

    explain_parser = subparsers.add_parser(
        "explain-query",
        help="Explain the typed query plan and safe candidate counts without returning content",
    )
    explain_parser.add_argument("query")
    _add_retrieval_args(explain_parser)
    _add_governance_args(explain_parser)

    evaluate_parser = subparsers.add_parser("evaluate", help="Run built-in prototype evaluation cases")
    evaluate_parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    evaluate_parser.add_argument("--db", type=Path, default=Path(".mka/eval.sqlite"))

    excel_preview_parser = subparsers.add_parser(
        "excel-preview",
        help="Create JSON and Markdown review previews from the marketing Excel workbook",
    )
    excel_preview_parser.add_argument("--workbook", type=Path, required=True)
    excel_preview_parser.add_argument("--output", type=Path, default=Path("reports/excel_preview"))
    excel_preview_parser.add_argument("--captured-date", type=_date_arg, default=None)

    asset_metadata_parser = subparsers.add_parser(
        "asset-metadata-preview",
        help="Build a read-only asset metadata inventory and enrichment review preview",
    )
    asset_metadata_parser.add_argument(
        "--preview-dir",
        type=Path,
        default=Path("reports/excel_preview"),
    )
    asset_metadata_parser.add_argument("--workbook", type=Path, default=None)
    asset_metadata_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    asset_metadata_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    asset_metadata_parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    asset_metadata_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/asset_metadata_preview"),
    )

    asset_review_parser = subparsers.add_parser(
        "validate-asset-review-decisions",
        help="Validate asset URL and identity review decisions without applying them",
    )
    asset_review_parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("reports/asset_metadata_preview/human_review_template.csv"),
    )
    asset_review_parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("reports/asset_metadata_preview/asset_metadata_inventory.csv"),
    )
    asset_review_parser.add_argument(
        "--enrichment",
        type=Path,
        default=Path("reports/asset_metadata_preview/asset_metadata_enrichment_preview.csv"),
    )
    asset_review_parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    )
    asset_review_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/asset_metadata_review_validation"),
    )

    asset_apply_parser = subparsers.add_parser(
        "apply-asset-review-decisions",
        help="Create a dry-run asset URL Apply Preview without changing formal data",
    )
    asset_apply_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Preview only; this command has no formal apply mode",
    )
    asset_apply_parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("reports/asset_metadata_preview/human_review_template.csv"),
    )
    asset_apply_parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("reports/asset_metadata_preview/asset_metadata_inventory.csv"),
    )
    asset_apply_parser.add_argument(
        "--enrichment",
        type=Path,
        default=Path("reports/asset_metadata_preview/asset_metadata_enrichment_preview.csv"),
    )
    asset_apply_parser.add_argument(
        "--validation-dir",
        type=Path,
        default=Path("reports/asset_metadata_review_validation"),
    )
    asset_apply_parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    )
    asset_apply_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    asset_apply_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    asset_apply_parser.add_argument("--workbook", type=Path, default=None)
    asset_apply_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview"),
    )

    asset_plan_parser = subparsers.add_parser(
        "apply-asset-metadata",
        help="Create a read-only formal asset metadata Apply Plan",
    )
    asset_plan_stage = asset_plan_parser.add_mutually_exclusive_group(required=True)
    asset_plan_stage.add_argument(
        "--plan",
        action="store_true",
        help="Create the only enabled stage: a read-only Apply Plan",
    )
    asset_plan_stage.add_argument(
        "--confirm",
        dest="confirm_plan_id",
        metavar="PLAN_ID",
        help="Reserved contract; disabled until a separately approved sprint",
    )
    asset_plan_stage.add_argument(
        "--execute",
        dest="execute_plan_id",
        metavar="PLAN_ID",
        help="Reserved contract; disabled until a separately approved sprint",
    )
    asset_plan_parser.add_argument(
        "--apply-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    asset_plan_parser.add_argument(
        "--blocked-preview",
        type=Path,
        default=Path(
            "reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"
        ),
    )
    asset_plan_parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("reports/asset_metadata_preview/asset_metadata_inventory.csv"),
    )
    asset_plan_parser.add_argument(
        "--parent-records",
        type=Path,
        default=Path("reports/excel_preview/merchant_cases.json"),
    )
    asset_plan_parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("reports/asset_metadata_preview/human_review_template.csv"),
    )
    asset_plan_parser.add_argument(
        "--validation-dir",
        type=Path,
        default=Path("reports/asset_metadata_review_validation"),
    )
    asset_plan_parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    )
    asset_plan_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    asset_plan_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    asset_plan_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/asset_metadata_apply_plan"),
    )

    missing_parent_parser = subparsers.add_parser(
        "diagnose-missing-formal-parents",
        help="Diagnose orphan assets and missing formal parents without applying repairs",
    )
    missing_parent_parser.add_argument(
        "--join-validation",
        type=Path,
        default=Path(
            "reports/asset_metadata_apply_plan/asset_source_record_join_validation.csv"
        ),
    )
    missing_parent_parser.add_argument(
        "--apply-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    missing_parent_parser.add_argument(
        "--parent-records",
        type=Path,
        default=Path("reports/excel_preview/merchant_cases.json"),
    )
    missing_parent_parser.add_argument(
        "--review-decisions",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    missing_parent_parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    )
    missing_parent_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    missing_parent_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    missing_parent_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/missing_formal_parent_diagnostic"),
    )

    resolution_parser = subparsers.add_parser(
        "preview-missing-parent-resolution",
        help="Preview human-approved parent and asset resolution without applying it",
    )
    resolution_parser.add_argument(
        "--parent-records",
        type=Path,
        default=Path("reports/excel_preview/merchant_cases.json"),
    )
    resolution_parser.add_argument(
        "--review-decisions",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    resolution_parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("reports/asset_metadata_preview/asset_metadata_inventory.csv"),
    )
    resolution_parser.add_argument(
        "--apply-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    resolution_parser.add_argument(
        "--blocked-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    )
    resolution_parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    )
    resolution_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    resolution_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    resolution_parser.add_argument(
        "--production-slack-renderer",
        type=Path,
        default=Path("src/marketing_knowledge_agent/slack_interface.py"),
    )
    resolution_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/missing_parent_resolution_preview"),
    )
    resolution_parser.add_argument(
        "--reviewed-at",
        default=None,
        help="Optional ISO 8601 timestamp for reproducible tests; defaults to local time",
    )

    resolution_validation_parser = subparsers.add_parser(
        "validate-missing-parent-resolution",
        help="Validate and plan missing-parent resolution without applying it",
    )
    resolution_validation_parser.add_argument(
        "--resolution-dir",
        type=Path,
        default=Path("reports/missing_parent_resolution_preview"),
    )
    resolution_validation_parser.add_argument(
        "--parent-records",
        type=Path,
        default=Path("reports/excel_preview/merchant_cases.json"),
    )
    resolution_validation_parser.add_argument(
        "--review-decisions",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    resolution_validation_parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("reports/asset_metadata_preview/asset_metadata_inventory.csv"),
    )
    resolution_validation_parser.add_argument(
        "--asset-apply-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    resolution_validation_parser.add_argument(
        "--asset-blocked-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    )
    resolution_validation_parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    )
    resolution_validation_parser.add_argument(
        "--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT
    )
    resolution_validation_parser.add_argument(
        "--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB
    )
    resolution_validation_parser.add_argument(
        "--production-slack-renderer",
        type=Path,
        default=Path("src/marketing_knowledge_agent/slack_interface.py"),
    )
    resolution_validation_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/missing_parent_resolution_apply_preview"),
    )

    storage_schema_parser = subparsers.add_parser(
        "preview-resolution-storage-schema",
        help="Validate resolution storage schemas using temporary fixtures only",
    )
    storage_schema_parser.add_argument(
        "--resolution-dir",
        type=Path,
        default=Path("reports/missing_parent_resolution_preview"),
    )
    storage_schema_parser.add_argument(
        "--parent-records",
        type=Path,
        default=Path("reports/excel_preview/merchant_cases.json"),
    )
    storage_schema_parser.add_argument(
        "--review-decisions",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    storage_schema_parser.add_argument(
        "--asset-apply-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    storage_schema_parser.add_argument(
        "--asset-blocked-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    )
    storage_schema_parser.add_argument(
        "--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT
    )
    storage_schema_parser.add_argument(
        "--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB
    )
    storage_schema_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/resolution_storage_schema_preview"),
    )

    decision_store_parser = subparsers.add_parser(
        "governance-decision-store",
        help="Inventory and plan an append-only Governance Decision Store",
    )
    decision_store_parser.add_argument(
        "--plan", action="store_true", required=True, help="Plan only; no execute mode exists"
    )
    decision_store_parser.add_argument(
        "--review-decisions",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    decision_store_parser.add_argument(
        "--merchant-cases",
        type=Path,
        default=Path("reports/excel_preview/merchant_cases.json"),
    )
    decision_store_parser.add_argument(
        "--public-metrics",
        type=Path,
        default=Path("reports/excel_preview/public_metrics.json"),
    )
    decision_store_parser.add_argument(
        "--pending-metrics",
        type=Path,
        default=Path("reports/excel_preview/pending_metrics.json"),
    )
    decision_store_parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=Path("reports/excel_preview/restricted_customers.json"),
    )
    decision_store_parser.add_argument(
        "--asset-decisions",
        type=Path,
        default=Path("reports/asset_metadata_preview/human_review_template.csv"),
    )
    decision_store_parser.add_argument(
        "--asset-validation",
        type=Path,
        default=Path("reports/asset_metadata_review_validation/review_decision_status.csv"),
    )
    decision_store_parser.add_argument(
        "--asset-apply-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    decision_store_parser.add_argument(
        "--asset-blocked-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    )
    decision_store_parser.add_argument(
        "--resolution-dir",
        type=Path,
        default=Path("reports/missing_parent_resolution_preview"),
    )
    decision_store_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    decision_store_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    decision_store_parser.add_argument(
        "--production-slack-renderer",
        type=Path,
        default=Path("src/marketing_knowledge_agent/slack_interface.py"),
    )
    decision_store_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/governance_decision_store_plan"),
    )

    regenerated_store_parser = subparsers.add_parser(
        "regenerate-governance-decision-store-plan",
        help="Regenerate a preview-only Decision Store Plan from the immutable Parent authority Bundle",
    )
    regenerated_store_parser.add_argument(
        "--bundle", type=Path, default=DEFAULT_PARENT_AUTHORITY_IMPORT_BUNDLE
    )
    regenerated_store_parser.add_argument(
        "--legacy-decisions",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    regenerated_store_parser.add_argument(
        "--merchant-cases",
        type=Path,
        default=Path("reports/excel_preview/merchant_cases.json"),
    )
    regenerated_store_parser.add_argument(
        "--asset-url-decisions",
        type=Path,
        default=Path("reports/asset_metadata_preview/human_review_template.csv"),
    )
    regenerated_store_parser.add_argument(
        "--asset-url-validation",
        type=Path,
        default=Path("reports/asset_metadata_review_validation/review_decision_status.csv"),
    )
    regenerated_store_parser.add_argument(
        "--asset-apply-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    regenerated_store_parser.add_argument(
        "--asset-blocked-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    )
    regenerated_store_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    regenerated_store_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    regenerated_store_parser.add_argument(
        "--production-slack-renderer",
        type=Path,
        default=Path("src/marketing_knowledge_agent/slack_interface.py"),
    )
    regenerated_store_parser.add_argument(
        "--target", type=Path, default=DEFAULT_GOVERNANCE_DECISION_STORE_TARGET
    )
    regenerated_store_parser.add_argument(
        "--output", type=Path, default=DEFAULT_REGENERATED_DECISION_STORE_PLAN_OUTPUT
    )
    regenerated_store_parser.add_argument(
        "--created-at",
        default=None,
        help="Optional timezone-aware ISO timestamp for deterministic dry-run output",
    )

    validate_store_plan_parser = subparsers.add_parser(
        "validate-governance-decision-store-plan",
        help="Independently validate the exact regenerated Decision Store Plan",
    )
    validate_store_plan_parser.add_argument("--plan-id", required=True)
    validate_store_plan_parser.add_argument("--manifest-hash", required=True)
    validate_store_plan_parser.add_argument(
        "--plan-manifest", type=Path, default=DEFAULT_CONFIRMATION_PLAN_MANIFEST
    )
    validate_store_plan_parser.add_argument(
        "--bundle", type=Path, default=DEFAULT_CONFIRMATION_BUNDLE_PATH
    )
    validate_store_plan_parser.add_argument(
        "--target", type=Path, default=DEFAULT_CONFIRMATION_FORMAL_TARGET
    )
    validate_store_plan_parser.add_argument(
        "--now", default=None, help="Optional timezone-aware validation timestamp"
    )

    confirm_store_plan_parser = subparsers.add_parser(
        "confirm-governance-decision-store-plan",
        help="Independently validate and immutably confirm the exact Decision Store Plan",
    )
    confirm_store_plan_parser.add_argument("--plan-id", required=True)
    confirm_store_plan_parser.add_argument("--manifest-hash", required=True)
    confirm_store_plan_parser.add_argument("--reviewer", required=True, choices=("Admin",))
    confirm_store_plan_parser.add_argument(
        "--confirmed-at",
        default=None,
        help="Optional timezone-aware confirmation timestamp",
    )
    confirm_store_plan_parser.add_argument(
        "--plan-manifest", type=Path, default=DEFAULT_CONFIRMATION_PLAN_MANIFEST
    )
    confirm_store_plan_parser.add_argument(
        "--bundle", type=Path, default=DEFAULT_CONFIRMATION_BUNDLE_PATH
    )
    confirm_store_plan_parser.add_argument(
        "--target", type=Path, default=DEFAULT_CONFIRMATION_FORMAL_TARGET
    )
    confirm_store_plan_parser.add_argument(
        "--confirmation-path", type=Path, default=DEFAULT_DECISION_STORE_CONFIRMATION_PATH
    )
    confirm_store_plan_parser.add_argument(
        "--output", type=Path, default=DEFAULT_DECISION_STORE_CONFIRMATION_REPORTS
    )

    execute_store_plan_parser = subparsers.add_parser(
        "execute-governance-decision-store-plan",
        help="Fail-closed execution preflight for the exact confirmed Decision Store Plan",
    )
    execute_store_plan_parser.add_argument("--plan-id", required=True)
    execute_store_plan_parser.add_argument("--manifest-hash", required=True)
    execute_store_plan_parser.add_argument("--confirmation-id", required=True)
    execute_store_plan_parser.add_argument("--confirmation-root-hash", required=True)
    execute_store_plan_parser.add_argument(
        "--target", type=Path, default=DEFAULT_DECISION_STORE_EXECUTION_TARGET
    )
    execute_store_plan_parser.add_argument(
        "--execution-bundle", type=Path, default=DEFAULT_DECISION_STORE_EXECUTION_BUNDLE
    )
    execute_store_plan_parser.add_argument(
        "--output", type=Path, default=DEFAULT_DECISION_STORE_EXECUTION_REPORTS
    )
    execute_store_plan_parser.add_argument(
        "--executed-at",
        default=None,
        help="Optional timezone-aware execution preflight timestamp",
    )

    schema_v2_parser = subparsers.add_parser(
        "plan-governance-decision-store-schema-v2",
        help="Build and validate a preview-only Governance Decision Store Schema V2 Plan",
    )
    schema_v2_parser.add_argument("--bundle", type=Path, default=DEFAULT_SCHEMA_V2_BUNDLE_PATH)
    schema_v2_parser.add_argument(
        "--old-plan-manifest", type=Path, default=DEFAULT_SCHEMA_V2_OLD_PLAN_MANIFEST
    )
    schema_v2_parser.add_argument(
        "--old-confirmation", type=Path, default=DEFAULT_SCHEMA_V2_OLD_CONFIRMATION
    )
    schema_v2_parser.add_argument(
        "--execute-reports", type=Path, default=DEFAULT_SCHEMA_V2_EXECUTE_REPORTS
    )
    schema_v2_parser.add_argument(
        "--legacy-decisions", type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    schema_v2_parser.add_argument(
        "--merchant-cases", type=Path, default=Path("reports/excel_preview/merchant_cases.json")
    )
    schema_v2_parser.add_argument(
        "--asset-url-decisions", type=Path,
        default=Path("reports/asset_metadata_preview/human_review_template.csv"),
    )
    schema_v2_parser.add_argument(
        "--asset-url-validation", type=Path,
        default=Path("reports/asset_metadata_review_validation/review_decision_status.csv"),
    )
    schema_v2_parser.add_argument(
        "--asset-apply-preview", type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    schema_v2_parser.add_argument(
        "--asset-blocked-preview", type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    )
    schema_v2_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    schema_v2_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    schema_v2_parser.add_argument(
        "--production-slack-renderer", type=Path,
        default=Path("src/marketing_knowledge_agent/slack_interface.py"),
    )
    schema_v2_parser.add_argument("--target", type=Path, default=DEFAULT_SCHEMA_V2_TARGET)
    schema_v2_parser.add_argument("--output", type=Path, default=DEFAULT_SCHEMA_V2_OUTPUT)
    schema_v2_parser.add_argument("--temporary-dir", type=Path, default=None)
    schema_v2_parser.add_argument(
        "--created-at", default=None,
        help="Optional timezone-aware ISO timestamp for deterministic dry-run output",
    )

    validate_schema_v2_parser = subparsers.add_parser(
        "validate-governance-decision-store-schema-v2-plan",
        help="Independently validate the exact Governance Decision Store Schema V2 Plan",
    )
    _add_schema_v2_confirmation_authority_args(validate_schema_v2_parser)
    validate_schema_v2_parser.add_argument("--now", default=None)

    confirm_schema_v2_parser = subparsers.add_parser(
        "confirm-governance-decision-store-schema-v2-plan",
        help="Independently validate and immutably confirm the exact Schema V2 Plan",
    )
    _add_schema_v2_confirmation_authority_args(confirm_schema_v2_parser)
    confirm_schema_v2_parser.add_argument("--reviewer", required=True, choices=("Admin",))
    confirm_schema_v2_parser.add_argument("--confirmed-at", default=None)
    confirm_schema_v2_parser.add_argument(
        "--confirmation-path", type=Path, default=DEFAULT_SCHEMA_V2_CONFIRMATION_PATH
    )
    confirm_schema_v2_parser.add_argument(
        "--output", type=Path, default=DEFAULT_SCHEMA_V2_CONFIRMATION_REPORTS
    )

    execute_schema_v2_parser = subparsers.add_parser(
        "execute-governance-decision-store-schema-v2-plan",
        help="Execute only the exact independently confirmed Schema V2 Plan",
    )
    execute_schema_v2_parser.add_argument("--plan-id", required=True)
    execute_schema_v2_parser.add_argument("--plan-manifest-hash", required=True)
    execute_schema_v2_parser.add_argument("--schema-hash", required=True)
    execute_schema_v2_parser.add_argument("--confirmation-id", required=True)
    execute_schema_v2_parser.add_argument("--confirmation-root-hash", required=True)
    execute_schema_v2_parser.add_argument(
        "--confirmation-bundle", type=Path, default=DEFAULT_SCHEMA_V2_CONFIRMATION_PATH
    )
    execute_schema_v2_parser.add_argument(
        "--target", type=Path, default=DEFAULT_SCHEMA_V2_EXECUTION_TARGET
    )
    execute_schema_v2_parser.add_argument(
        "--execution-bundle", type=Path, default=DEFAULT_SCHEMA_V2_EXECUTION_BUNDLE
    )
    execute_schema_v2_parser.add_argument(
        "--output", type=Path, default=DEFAULT_SCHEMA_V2_EXECUTION_REPORTS
    )
    execute_schema_v2_parser.add_argument("--temporary-root", type=Path, default=None)
    execute_schema_v2_parser.add_argument("--executed-at", default=None)

    existing_store_parser = subparsers.add_parser(
        "validate-existing-governance-decision-store",
        help="Independently validate the existing Governance Decision Store read-only",
    )
    existing_store_parser.add_argument(
        "--database", type=Path, default=DEFAULT_EXISTING_DECISION_STORE_TARGET
    )
    existing_store_parser.add_argument(
        "--output", type=Path, default=DEFAULT_EXISTING_DECISION_STORE_REPORTS
    )
    existing_store_parser.add_argument("--temporary-root", type=Path, default=None)

    parent_sync_parser = subparsers.add_parser(
        "plan-parent-sync",
        help="Build a read-only full Parent projection and delta-only Sync Plan",
    )
    parent_sync_parser.add_argument(
        "--decision-store", type=Path, default=DEFAULT_PARENT_SYNC_DECISION_STORE
    )
    parent_sync_parser.add_argument(
        "--output", type=Path, default=DEFAULT_PARENT_SYNC_OUTPUT
    )
    parent_sync_parser.add_argument("--temporary-root", type=Path, default=None)
    parent_sync_parser.add_argument("--created-at", default=None)

    validate_parent_sync_parser = subparsers.add_parser(
        "validate-parent-sync-plan",
        help="Independently validate the exact Parent Sync Plan without syncing",
    )
    validate_parent_sync_parser.add_argument("--plan-id", required=True)
    validate_parent_sync_parser.add_argument("--manifest-hash", required=True)
    validate_parent_sync_parser.add_argument("--temporary-root", type=Path, default=None)
    validate_parent_sync_parser.add_argument("--validated-at", default=None)

    confirm_parent_sync_parser = subparsers.add_parser(
        "confirm-parent-sync-plan",
        help="Validate and confirm only an exact eligible Parent Sync Plan without executing it",
    )
    confirm_parent_sync_parser.add_argument("--plan-id", required=True)
    confirm_parent_sync_parser.add_argument("--manifest-hash", required=True)
    confirm_parent_sync_parser.add_argument("--reviewer", required=True, choices=("Admin",))
    confirm_parent_sync_parser.add_argument("--confirmed-at", default=None)
    confirm_parent_sync_parser.add_argument(
        "--confirmation-path", type=Path, default=DEFAULT_PARENT_SYNC_CONFIRMATION_PATH
    )
    confirm_parent_sync_parser.add_argument(
        "--output", type=Path, default=DEFAULT_PARENT_SYNC_CONFIRMATION_REPORTS
    )
    confirm_parent_sync_parser.add_argument("--temporary-root", type=Path, default=None)

    parent_authority_parser = subparsers.add_parser(
        "prepare-parent-authority-review",
        help="Prepare a read-only Admin review packet for Parent authority gaps",
    )
    parent_authority_parser.add_argument(
        "--merchant-cases",
        type=Path,
        default=Path("reports/excel_preview/merchant_cases.json"),
    )
    parent_authority_parser.add_argument(
        "--review-decisions",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    parent_authority_parser.add_argument(
        "--admin-resolutions",
        type=Path,
        default=Path("reports/missing_parent_resolution_preview/missing_parent_resolution_decisions.csv"),
    )
    parent_authority_parser.add_argument(
        "--baseline-import-preview",
        type=Path,
        default=Path("reports/governance_decision_store_plan/baseline_import_preview.csv"),
    )
    parent_authority_parser.add_argument(
        "--decision-source-inventory",
        type=Path,
        default=Path("reports/governance_decision_store_plan/decision_source_inventory.csv"),
    )
    parent_authority_parser.add_argument(
        "--asset-inventory",
        type=Path,
        default=Path("reports/asset_metadata_preview/asset_metadata_inventory.csv"),
    )
    parent_authority_parser.add_argument(
        "--asset-resolution",
        type=Path,
        default=Path("reports/missing_parent_resolution_preview/asset_eligibility_preview.csv"),
    )
    parent_authority_parser.add_argument(
        "--asset-url-decisions",
        type=Path,
        default=Path("reports/asset_metadata_preview/human_review_template.csv"),
    )
    parent_authority_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    parent_authority_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    parent_authority_parser.add_argument(
        "--decision-store",
        type=Path,
        default=Path("data/governance/governance_decisions.sqlite"),
    )
    parent_authority_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/parent_baseline_authority_review"),
    )

    parent_bundle_parser = subparsers.add_parser(
        "create-parent-authority-import-bundle",
        help="Create an immutable Parent authority approval evidence bundle",
    )
    parent_bundle_parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_PARENT_AUTHORITY_BUNDLE_PATH,
    )
    parent_bundle_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_PARENT_AUTHORITY_BUNDLE_REPORT_DIR,
    )
    parent_bundle_parser.add_argument(
        "--created-at",
        default=None,
        help="Optional timezone-aware ISO timestamp for deterministic creation",
    )

    validate_parent_bundle_parser = subparsers.add_parser(
        "validate-parent-authority-import-bundle",
        help="Validate an immutable Parent authority approval evidence bundle",
    )
    validate_parent_bundle_parser.add_argument(
        "--bundle",
        type=Path,
        default=DEFAULT_PARENT_AUTHORITY_BUNDLE_PATH,
    )
    decision_store_parser.add_argument(
        "--created-at",
        default=None,
        help="Optional timezone-aware ISO timestamp for deterministic dry-run output",
    )

    slack_preview_parser = subparsers.add_parser(
        "preview-slack-output",
        help="Render offline Slack format previews without calling Slack or applying asset URLs",
    )
    preview_mode = slack_preview_parser.add_mutually_exclusive_group(required=True)
    preview_mode.add_argument("--query")
    preview_mode.add_argument("--sample-set", action="store_true")
    slack_preview_parser.add_argument(
        "--variant",
        choices=("concise", "standard", "detailed"),
        default="standard",
    )
    slack_preview_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    slack_preview_parser.add_argument(
        "--apply-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview.csv"),
    )
    slack_preview_parser.add_argument(
        "--blocked-preview",
        type=Path,
        default=Path("reports/asset_metadata_apply_preview/asset_apply_preview_blocked.csv"),
    )
    slack_preview_parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("reports/asset_metadata_preview/human_review_template.csv"),
    )
    slack_preview_parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
    )
    slack_preview_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    slack_preview_parser.add_argument("--workbook", type=Path, default=None)
    slack_preview_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/slack_output_preview"),
    )

    review_template_parser = subparsers.add_parser(
        "review-template",
        help="Create a human review decisions CSV from Excel preview JSON files",
    )
    review_template_parser.add_argument("--preview-dir", type=Path, default=Path("reports/excel_preview"))
    review_template_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_template.csv"),
    )
    review_template_parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("reports/excel_preview/review_summary.md"),
    )

    validate_decisions_parser = subparsers.add_parser(
        "validate-review-decisions",
        help="Validate a human-filled review decisions CSV without applying decisions",
    )
    validate_decisions_parser.add_argument("--decisions", type=Path, required=True)
    validate_decisions_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/excel_preview/review_decisions_validation_summary.md"),
    )
    validate_decisions_parser.add_argument("--preview-dir", type=Path, default=Path("reports/excel_preview"))

    apply_decisions_parser = subparsers.add_parser(
        "apply-review-decisions",
        help="Apply reviewed decisions into preview-only Vault/governance outputs",
    )
    apply_decisions_parser.add_argument("--decisions", type=Path, required=True)
    apply_decisions_parser.add_argument("--preview-dir", type=Path, default=Path("reports/excel_preview"))
    apply_decisions_parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/excel_preview/apply_preview"),
    )
    clean_record_group = apply_decisions_parser.add_mutually_exclusive_group()
    clean_record_group.add_argument("--include-clean-records", action="store_true")
    clean_record_group.add_argument("--include-clean-merchant-cases", action="store_true")

    sync_parser = subparsers.add_parser(
        "sync-obsidian",
        help="Plan, execute, or rollback preview-only content sync into an Obsidian namespace",
    )
    sync_subparsers = sync_parser.add_subparsers(dest="sync_action", required=True)

    sync_plan_parser = sync_subparsers.add_parser("plan", help="Create a read-only Obsidian sync plan")
    sync_plan_parser.add_argument("--apply-dir", type=Path, required=True)
    sync_plan_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    sync_plan_parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)

    sync_execute_parser = sync_subparsers.add_parser("execute", help="Execute a confirmed Obsidian sync plan")
    sync_execute_parser.add_argument("--plan", type=Path, required=True)
    sync_execute_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    sync_execute_parser.add_argument("--confirm", action="store_true")
    sync_execute_parser.add_argument("--allow-conflicts-skip", action="store_true")

    sync_rollback_parser = sync_subparsers.add_parser("rollback", help="Rollback a previous Obsidian sync batch")
    sync_rollback_parser.add_argument("--batch", required=True)
    sync_rollback_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)

    content_index_parser = subparsers.add_parser(
        "build-content-index",
        help="Plan or build the formal SQLite content index from managed Obsidian content",
    )
    content_index_parser.add_argument("--vault", type=Path, default=DEFAULT_OBSIDIAN_VAULT)
    content_index_parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    content_index_parser.add_argument("--db", type=Path, default=DEFAULT_CONTENT_INDEX_DB)
    content_index_parser.add_argument("--report-dir", type=Path, default=DEFAULT_CONTENT_INDEX_REPORT_DIR)
    content_index_parser.add_argument("--confirm", action="store_true")

    slack_parser = subparsers.add_parser(
        "slack-bot",
        help="Start the external-only Slack Socket Mode interface",
    )
    slack_parser.add_argument("--config", type=Path, default=Path(".mka/slack_config.json"))

    return parser


def _add_schema_v2_confirmation_authority_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--manifest-hash", required=True)
    parser.add_argument("--schema-hash", required=True)
    parser.add_argument("--canonical-sql-hash", required=True)
    parser.add_argument(
        "--plan-manifest", type=Path, default=DEFAULT_SCHEMA_V2_CONFIRMATION_PLAN
    )
    parser.add_argument(
        "--canonical-schema", type=Path, default=DEFAULT_SCHEMA_V2_CONFIRMATION_SCHEMA
    )
    parser.add_argument(
        "--schema-hash-file", type=Path,
        default=DEFAULT_SCHEMA_V2_CONFIRMATION_HASH_FILE,
    )
    parser.add_argument("--bundle", type=Path, default=DEFAULT_SCHEMA_V2_CONFIRMATION_BUNDLE)
    parser.add_argument(
        "--old-confirmation", type=Path, default=DEFAULT_SCHEMA_V2_HISTORICAL_CONFIRMATION
    )
    parser.add_argument("--target", type=Path, default=DEFAULT_SCHEMA_V2_CONFIRMATION_TARGET)
    parser.add_argument("--temporary-root", type=Path, default=None)


def _add_retrieval_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--mode", choices=["keyword", "vector", "hybrid"], default="hybrid")
    parser.add_argument(
        "--intent",
        choices=["internal", "external"],
        default="internal",
        help="Query use: external = 只回可對外引用內容",
    )
    parser.add_argument("--record-type", action="append", default=[])
    parser.add_argument("--source-type", action="append", default=[])
    parser.add_argument("--content-category", action="append", default=[])
    parser.add_argument("--parent-source-type", action="append", default=[])
    parser.add_argument("--brand-name", action="append", default=[])
    parser.add_argument("--merchant-handle", action="append", default=[])
    parser.add_argument("--merchant-status", action="append", default=[])
    parser.add_argument("--product", action="append", default=[])
    parser.add_argument("--industry", action="append", default=[])
    parser.add_argument("--sales-category-lv1", action="append", default=[])
    parser.add_argument("--sales-category-lv2", action="append", default=[])
    parser.add_argument("--content-tag", action="append", default=[])
    parser.add_argument("--topic", action="append", default=[])
    parser.add_argument("--metric-type", action="append", default=[])
    parser.add_argument("--metric-name", action="append", default=[])
    parser.add_argument("--claim-status", action="append", default=[])
    parser.add_argument("--data-classification", action="append", default=[])
    parser.add_argument("--exposure-channel", action="append", default=[])
    parser.add_argument("--funnel-stage", action="append", default=[])
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--can-quote-externally", action=argparse.BooleanOptionalAction, default=None)


def _add_governance_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--restricted-customers",
        type=Path,
        default=DEFAULT_RESTRICTED_CUSTOMERS_PATH,
        help="Path to restricted customer denylist preview JSON",
    )


def _add_llm_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=["mock", "anthropic"],
        default="mock",
        help="Text generation provider; mock keeps all processing offline",
    )
    parser.add_argument(
        "--dry-run-llm",
        "--show-llm-payload",
        dest="dry_run_llm",
        action="store_true",
        help="Print the minimized LLM payload without calling any provider",
    )


def _filters_from_args(args: argparse.Namespace) -> SearchFilters:
    return SearchFilters(
        intent=args.intent,
        record_type=args.record_type,
        source_type=args.source_type,
        content_category=args.content_category,
        parent_source_type=args.parent_source_type,
        brand_name=args.brand_name,
        merchant_handle=args.merchant_handle,
        merchant_status=args.merchant_status,
        product=args.product,
        industry=args.industry,
        sales_category_lv1=args.sales_category_lv1,
        sales_category_lv2=args.sales_category_lv2,
        content_tags=args.content_tag,
        topic=args.topic,
        metric_type=args.metric_type,
        metric_name=args.metric_name,
        claim_status=args.claim_status,
        data_classification=args.data_classification,
        exposure_channel=args.exposure_channel,
        funnel_stage=args.funnel_stage,
        status=args.status,
        can_quote_externally=args.can_quote_externally,
    )


def _print_answer(answer) -> None:
    print(answer.answer)
    if not getattr(answer, "governance_checked", False):
        print("\n!!! WARNING: restricted denylist 未完成檢查，請勿直接對外使用。")
    print("\nCitations:")
    for citation in answer.citations:
        print(
            f"- {citation.label} {citation.title} "
            f"({citation.source_type}, record_type={citation.record_type}, "
            f"classification={citation.data_classification}, "
            f"can_quote={citation.can_quote_externally}, status={citation.status}) "
            f"{citation.source_path}; sheet={citation.source_sheet}, row={citation.source_row}; "
            f"{citation.freshness_note}"
        )
    if answer.warnings:
        print("\nWarnings:")
        for warning in answer.warnings:
            print(f"- {warning}")


def _plan_has_conflicts(payload: dict) -> bool:
    counts = payload.get("counts", {})
    return bool(counts.get("conflict_user_edited", 0) or counts.get("conflict_unmanaged", 0))


def _print_sync_summary(payload: dict) -> None:
    summary = {
        key: payload[key]
        for key in ("status", "requires_confirmation", "json_path", "markdown_path", "manifest_path", "batch_id", "counts")
        if key in payload
    }
    if "message" in payload:
        summary["message"] = payload["message"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _date_arg(value: str):
    from datetime import date

    return date.fromisoformat(value)


if __name__ == "__main__":
    raise SystemExit(main())
