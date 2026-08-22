# ⚠️ DEPRECATED DIRECTORY

This `backend/` directory is **NO LONGER MAINTAINED** and will be removed in a future release.

## Migration Guide

### Canonical Backend Location
- **OLD**: `backend/main.py` 
- **NEW**: Repository root `main.py`

### Canonical RTL-SDR Implementation  
- **OLD**: `backend/rtlsdr_reader.py`
- **NEW**: `rf/rtlsdr_reader.py`

### Why This Change?

The repository previously had duplicate backend implementations that caused:
- Confusion about which file to modify
- Inconsistent security models (wildcard CORS vs controlled origins)
- Divergent feature sets between implementations
- Maintenance burden of keeping two implementations in sync

The root `main.py` is now the single authoritative implementation with:
- Proper security (API_KEY authentication on WebSockets)
- Controlled CORS origins from config.py
- Recent bug fixes and improvements
- Active maintenance

### Actions Required

1. **Update imports**: Change any `from backend.main import ...` to `from main import ...`
2. **Update documentation**: Remove references to `backend/main.py`
3. **Update scripts**: Change any scripts that run `backend/main.py` to use root `main.py`
4. **Update Docker configs**: The docker-compose.yml already uses the correct root main.py

### Timeline

- **Current**: Directory marked as deprecated
- **Next major release**: Directory will be removed entirely
- **Migration assistance**: Please open an issue if you need help migrating

### Files Still Referenced

If you find any files still importing from this directory, please update them or report them as issues.

---

**Date**: 2026-08-22  
**Reason**: Repository consolidation to eliminate duplicate implementations  
**Contact**: Open a GitHub issue for migration assistance