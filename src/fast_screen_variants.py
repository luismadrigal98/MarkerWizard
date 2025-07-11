#!/usr/bin/env python3
"""
Fast variant screening with vectorized operations and parallel processing

This optimized version replaces the slow nested loops with vectorized pandas operations
and optional multiprocessing for large datasets.

@author: Luis Javier Madrigal-Roca & John K. Kelly
@date: 2025/06/12
"""

import pandas as pd
import numpy as np
import logging
from tqdm import tqdm
import multiprocessing as mp
from functools import partial

def fast_screen_single_chromosome(chrom_data, primer_size=20, amplicon_size=300, displacement_steps=5):
    """
    Fast screening for a single chromosome using vectorized operations
    """
    df = chrom_data.copy()
    positions = df['POS'].values
    n_variants = len(positions)
    
    # Initialize results
    df['primer_compliant'] = False
    df['amplicon_start'] = None
    df['amplicon_end'] = None
    df['displacement'] = 0
    
    # Vectorized amplicon boundary calculation
    amplicon_starts = positions - (amplicon_size // 2)
    amplicon_ends = positions + (amplicon_size // 2)
    
    # For each variant, check conflicts efficiently
    for i in range(n_variants):
        pos = positions[i]
        amp_start = amplicon_starts[i]
        amp_end = amplicon_ends[i]
        
        # Define primer regions
        f_primer_start = amp_start
        f_primer_end = amp_start + primer_size
        r_primer_start = amp_end - primer_size
        r_primer_end = amp_end
        
        # Check for conflicts using vectorized operations
        # Exclude the current variant position
        other_positions = positions[positions != pos]
        
        # Check if any other variants fall in primer regions
        f_conflicts = np.any((other_positions >= f_primer_start) & (other_positions <= f_primer_end))
        r_conflicts = np.any((other_positions >= r_primer_start) & (other_positions <= r_primer_end))
        
        if not f_conflicts and not r_conflicts:
            df.iloc[i, df.columns.get_loc('primer_compliant')] = True
            df.iloc[i, df.columns.get_loc('amplicon_start')] = amp_start
            df.iloc[i, df.columns.get_loc('amplicon_end')] = amp_end
            continue
        
        # Try displacement if initial placement failed
        found_placement = False
        for step in range(1, displacement_steps + 1):
            for direction in [-1, 1]:  # left then right
                shift = direction * step
                new_amp_start = amp_start + shift
                new_amp_end = amp_end + shift
                
                new_f_start = new_amp_start
                new_f_end = new_amp_start + primer_size
                new_r_start = new_amp_end - primer_size
                new_r_end = new_amp_end
                
                # Check if variant still in amplicon
                if not (new_amp_start <= pos <= new_amp_end):
                    continue
                    
                # Check conflicts with new placement
                f_conflicts = np.any((other_positions >= new_f_start) & (other_positions <= new_f_end))
                r_conflicts = np.any((other_positions >= new_r_start) & (other_positions <= new_r_end))
                
                if not f_conflicts and not r_conflicts:
                    df.iloc[i, df.columns.get_loc('primer_compliant')] = True
                    df.iloc[i, df.columns.get_loc('amplicon_start')] = new_amp_start
                    df.iloc[i, df.columns.get_loc('amplicon_end')] = new_amp_end
                    df.iloc[i, df.columns.get_loc('displacement')] = shift
                    found_placement = True
                    break
            if found_placement:
                break
    
    return df

def fast_screen_variants_parallel(df, primer_size=20, amplicon_size=300, displacement_steps=5, 
                                 n_workers=None):
    """
    Fast parallel variant screening by chromosome
    """
    if n_workers is None:
        n_workers = min(mp.cpu_count() - 1, 4)  # Don't overwhelm the system
    
    # Group by chromosome for parallel processing
    chrom_groups = [group for name, group in df.groupby('CHROM')]
    
    if len(chrom_groups) == 1 or n_workers == 1:
        # Single chromosome or no parallelization
        logging.info("Processing single chromosome sequentially")
        return fast_screen_single_chromosome(df, primer_size, amplicon_size, displacement_steps)
    
    # Parallel processing by chromosome
    logging.info(f"Processing {len(chrom_groups)} chromosomes with {n_workers} workers")
    
    screen_func = partial(fast_screen_single_chromosome, 
                         primer_size=primer_size, 
                         amplicon_size=amplicon_size,
                         displacement_steps=displacement_steps)
    
    with mp.Pool(n_workers) as pool:
        results = list(tqdm(
            pool.imap(screen_func, chrom_groups),
            total=len(chrom_groups),
            desc="Screening chromosomes"
        ))
    
    # Combine results
    return pd.concat(results, ignore_index=True)

def fast_filter_diagnostic_variants(df, target_parent='664c'):
    """
    Fast vectorized filtering for diagnostic variants
    """
    # Find allele columns
    allele_cols = [col for col in df.columns if col.endswith('_allele')]
    target_col = f"{target_parent}_allele"
    
    if target_col not in allele_cols:
        logging.error(f"Target parent column '{target_col}' not found!")
        return pd.DataFrame()
    
    other_cols = [col for col in allele_cols if col != target_col]
    
    # Vectorized diagnostic filtering
    target_alleles = df[target_col]
    
    # Check that target allele is not 'N' or missing
    valid_target = (target_alleles != 'N') & (target_alleles.notna())
    
    # Check that target differs from all other parents
    is_diagnostic = valid_target
    for other_col in other_cols:
        other_alleles = df[other_col]
        # Target must differ from this parent and parent must not be 'N'
        differs = (target_alleles != other_alleles) & (other_alleles != 'N') & (other_alleles.notna())
        is_diagnostic = is_diagnostic & differs
    
    return df[is_diagnostic].copy()

def fast_apply_spacing_filter(df, min_spacing=1000):
    """
    Fast spacing filter using vectorized operations
    """
    if len(df) == 0:
        return df
        
    # Sort by chromosome and position
    df_sorted = df.sort_values(['CHROM', 'POS']).copy()
    
    # Initialize selection mask
    selected_mask = np.zeros(len(df_sorted), dtype=bool)
    
    last_pos = {}  # Track last position per chromosome
    
    for i, row in enumerate(df_sorted.itertuples()):
        chrom = row.CHROM
        pos = row.POS
        
        if chrom not in last_pos or (pos - last_pos[chrom]) >= min_spacing:
            selected_mask[i] = True
            last_pos[chrom] = pos
    
    return df_sorted[selected_mask].copy()

def fast_quality_score(row, target_parent='664c'):
    """
    Calculate a fast quality score for variant ranking
    """
    reliability_values = {'low': 1, 'medium': 2, 'high': 3}
    
    score = reliability_values.get(row['overall_reliability'], 1)
    
    # Bonus for complete information
    if row.get('complete_info', False):
        score += 2
    
    # Bonus for F2 data availability
    if row.get('has_f2_data', False):
        score += 1
    
    # QUAL score component (normalized)
    if pd.notna(row.get('QUAL', 0)):
        score += min(2, row['QUAL'] / 100)
    
    # Bonus for primer compliance
    if row.get('primer_compliant', False):
        score += 1
    
    return score

def fast_comprehensive_screen(df, target_parent='664c', min_reliability='medium', 
                             min_spacing=2000, max_snps=50, primer_size=20, 
                             amplicon_size=300, displacement_steps=5, n_workers=None):
    """
    Comprehensive fast screening pipeline
    """
    logging.info(f"Starting comprehensive screening of {len(df)} variants")
    
    # Step 1: Quality filtering
    reliability_values = {'low': 1, 'medium': 2, 'high': 3}
    min_rel_value = reliability_values[min_reliability]
    
    quality_mask = (
        (df['complete_info'] == True) &
        (df['overall_reliability'].map(reliability_values) >= min_rel_value) &
        (df['has_f2_data'] == True)
    )
    
    filtered = df[quality_mask].copy()
    logging.info(f"After quality filtering: {len(filtered)} variants")
    
    if len(filtered) == 0:
        return pd.DataFrame()
    
    # Step 2: Diagnostic filtering
    diagnostic = fast_filter_diagnostic_variants(filtered, target_parent)
    logging.info(f"After diagnostic filtering: {len(diagnostic)} variants")
    
    if len(diagnostic) == 0:
        return pd.DataFrame()
    
    # Step 3: Primer screening
    screened = fast_screen_variants_parallel(
        diagnostic, 
        primer_size=primer_size,
        amplicon_size=amplicon_size,
        displacement_steps=displacement_steps,
        n_workers=n_workers
    )
    
    primer_compliant = screened[screened['primer_compliant'] == True].copy()
    logging.info(f"After primer screening: {len(primer_compliant)} variants")
    
    if len(primer_compliant) == 0:
        # Fallback to diagnostic variants without primer compliance
        primer_compliant = diagnostic.copy()
        logging.warning("No primer compliant variants found, using all diagnostic variants")
    
    # Step 4: Spacing filter
    spaced = fast_apply_spacing_filter(primer_compliant, min_spacing)
    logging.info(f"After spacing filter: {len(spaced)} variants")
    
    if len(spaced) == 0:
        return pd.DataFrame()
    
    # Step 5: Quality scoring and selection
    spaced['quality_score'] = spaced.apply(
        lambda row: fast_quality_score(row, target_parent), axis=1
    )
    
    # Sort and select top variants
    final = spaced.sort_values(['quality_score', 'QUAL'], ascending=[False, False])
    
    if len(final) > max_snps:
        final = final.head(max_snps)
        logging.info(f"Selected top {max_snps} variants")
    
    return final

# Convenience function for backwards compatibility
def screen_variants_fast(df, **kwargs):
    """
    Backwards compatible wrapper
    """
    return fast_comprehensive_screen(df, **kwargs)