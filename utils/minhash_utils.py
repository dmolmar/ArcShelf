"""
MinHash utilities for fast approximate Jaccard similarity computation.

MinHash generates compact signatures that can estimate Jaccard similarity
between tag sets much faster than computing exact intersection/union.
"""
import struct
from typing import Set, Optional
from datasketch import MinHash

# Number of hash permutations. Higher = more accurate but larger signatures.
# 128 gives ~97% accuracy for Jaccard estimates, good balance of speed/accuracy.
NUM_PERMUTATIONS = 128


def compute_minhash_signature(tags: Set[str]) -> bytes:
    """
    Computes a MinHash signature for a set of tags.
    
    Args:
        tags: Set of tag strings to hash
        
    Returns:
        Binary representation of the MinHash signature (NUM_PERMUTATIONS * 4 bytes)
    """
    if not tags:
        # Return empty signature for empty tag sets
        return b''
    
    mh = MinHash(num_perm=NUM_PERMUTATIONS)
    for tag in tags:
        mh.update(tag.encode('utf-8'))
    
    # Pack the hash values as unsigned 32-bit integers
    return struct.pack(f'{NUM_PERMUTATIONS}I', *mh.hashvalues)


def signature_to_minhash(signature: bytes) -> Optional[MinHash]:
    """
    Reconstructs a MinHash object from a binary signature.
    
    Args:
        signature: Binary signature from compute_minhash_signature()
        
    Returns:
        MinHash object, or None if signature is invalid/empty
    """
    if not signature or len(signature) != NUM_PERMUTATIONS * 4:
        return None
    
    mh = MinHash(num_perm=NUM_PERMUTATIONS)
    mh.hashvalues = list(struct.unpack(f'{NUM_PERMUTATIONS}I', signature))
    return mh


def estimate_jaccard_from_signatures(sig1: bytes, sig2: bytes) -> float:
    """
    Estimates Jaccard similarity from two MinHash signatures.
    
    This is much faster than computing exact Jaccard similarity:
    - Exact: O(|tags1| + |tags2|) set operations
    - MinHash: O(NUM_PERMUTATIONS) integer comparisons
    
    Args:
        sig1: First signature
        sig2: Second signature
        
    Returns:
        Estimated Jaccard similarity (0.0 to 1.0), or 0.0 if signatures invalid
    """
    mh1 = signature_to_minhash(sig1)
    mh2 = signature_to_minhash(sig2)
    
    if mh1 is None or mh2 is None:
        return 0.0
    
    return mh1.jaccard(mh2)


def estimate_jaccard_fast(sig1: bytes, sig2: bytes) -> float:
    """
    Ultra-fast Jaccard estimation by directly comparing packed integers.
    
    Avoids MinHash object reconstruction for maximum speed.
    
    Args:
        sig1: First signature
        sig2: Second signature
        
    Returns:
        Estimated Jaccard similarity (0.0 to 1.0)
    """
    if not sig1 or not sig2:
        return 0.0
    
    if len(sig1) != NUM_PERMUTATIONS * 4 or len(sig2) != NUM_PERMUTATIONS * 4:
        return 0.0
    
    # Unpack both signatures
    vals1 = struct.unpack(f'{NUM_PERMUTATIONS}I', sig1)
    vals2 = struct.unpack(f'{NUM_PERMUTATIONS}I', sig2)
    
    # Count matching hash values
    matches = sum(1 for v1, v2 in zip(vals1, vals2) if v1 == v2)
    
    return matches / NUM_PERMUTATIONS
