"""Materialize a hash-linked PIT R103 feature extension."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
import sys
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etf_tricks.tier1.artifact import write_feature_extension_artifact
from etf_tricks.tier1.chip_feature_artifact import merge_chip_feature_extension
from etf_tricks.tier1.roe_feature_extension import Tier1RoeFeatureExtensionBuilder


def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument('--afml-root',required=True); p.add_argument('--base-extension-root',required=True)
    p.add_argument('--holdings-path',required=True); p.add_argument('--roe-root',required=True); p.add_argument('--output-root',required=True)
    a=p.parse_args(); afml=Path(a.afml_root); base=Path(a.base_extension_root); roe_root=Path(a.roe_root); out=Path(a.output_root)
    manifest=json.loads((roe_root/'manifests'/'afml_roe_etf_constituents.json').read_text())
    safe=[roe_root/path for path in manifest['artifact_paths']]
    if not all(path.is_file() for path in safe): raise ValueError('roe manifest lists missing partition')
    bars=pd.read_parquet(afml/'tables'/'dollar_bars.parquet')
    holdings=pd.read_parquet(a.holdings_path)
    roe=pd.concat([pd.read_parquet(path) for path in safe],ignore_index=True)
    sidecar=Tier1RoeFeatureExtensionBuilder().build(bars,holdings,roe)
    features=merge_chip_feature_extension(pd.read_parquet(base/'features.parquet'),sidecar)
    write_feature_extension_artifact(features,out,{'afml_manifest_sha256':_sha(afml/'manifest.json'),'base_extension_manifest_sha256':_sha(base/'manifest.json'),'roe_manifest_sha256':_sha(roe_root/'manifests'/'afml_roe_etf_constituents.json'),'feature_policy':'r103_ttm_merged_ntd_after_close_v1'})

if __name__=='__main__': main()
