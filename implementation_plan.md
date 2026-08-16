# Oprava asynchronního klienta, typizace dim odkazů a sanity checků

Navrhuji úpravy, které vyřeší obě kritické chyby s asynchronním stahováním `dim` odkazů a s falešným poplachem sanity checku pro `mukekodezu`. Zahrnuji také refaktor `rwp="dim_marker"` do explicitní vlastnosti.

## Open Questions
- U smyčky `while ... redirects < 5` v `resolver.py` se ptáš, jestli to bylo skutečně pozorováno. Nepozorovali jsme sice u Mapy.cz řetězení redirectů, ale kód byl zjevně psán defenzivně. Nicméně teď se ho nedotýkám, pokud vyloženě nechceš.
- Mám odstranit 20km sanity check úplně, nebo jen přidat parametr `is_trace` do `decode_mapy_geometry`? Můj návrh je smazat 20km skok z `decode_mapy_geometry` kompletně – kontextový `totalLength` check (v toleranci 2%) pro `dim` trasy ho bohatě vynahrazuje a chytí jak pomalý drift, tak velké skoky, zatímco plánovače na hrubých bodech limitem vzdálenosti trpět nebudou.

## Proposed Changes

### `mapy_gpx_exporter` Core Models

#### [MODIFY] [models.py](file:///home/pepa/Desktop/vscoderepo/mapy-gpx-exporter/src/mapy_gpx_exporter/models.py)
- Přidám pole `dim_id: str | None = None` do `RouteParams` jako explicitní signál pro odkaz typu "Saved Route" namísto magického stringu.

#### [MODIFY] [resolver.py](file:///home/pepa/Desktop/vscoderepo/mapy-gpx-exporter/src/mapy_gpx_exporter/resolver.py)
- V `parse_route_from_location` změním návratovou hodnotu pro `dim` odkazy na `return RouteParams(dim_id=dim, profile_code=profile_code)`. Odstraním `rwp="dim_marker"`.
- Funkce `resolve_short_link` se bude rozhodovat přes explicitní stav: `if params.dim_id: return resolve_dim_link(...)`.

### `mapy_gpx_exporter` FRPC & Async Resolution

#### [MODIFY] [frpc_resolver.py](file:///home/pepa/Desktop/vscoderepo/mapy-gpx-exporter/src/mapy_gpx_exporter/frpc_resolver.py)
- Abych neopakoval 150 řádků logiky parsování FRPC objektu (heuristiky typů tras atd.) pro synchronního a asynchronního klienta, extrahuji request přípravu a parsování do čistých, privátních funkcí:
  - `_prepare_dim_request(location, dim_id)`
  - `_parse_dim_response(content, dim_id)`
- Existující `resolve_dim_link` provede synchronní `client.post` a zavolá parser.
- Přidám novou metodu `async_resolve_dim_link(client: httpx.AsyncClient, ...)` s použitím `await client.post`.

#### [MODIFY] [client.py](file:///home/pepa/Desktop/vscoderepo/mapy-gpx-exporter/src/mapy_gpx_exporter/client.py)
- `AsyncMapyGpxClient._resolve` zkontroluje `if params.dim_id:` a případně použije `await async_resolve_dim_link`.
- Tím se opraví kritický bug asynchronního/batch klienta, který předtím `dim` links rovnou sypal na GPX endpoint a tvořil nesmysly.

### `mapy_gpx_exporter` Decoder

#### [MODIFY] [decoder.py](file:///home/pepa/Desktop/vscoderepo/mapy-gpx-exporter/src/mapy_gpx_exporter/decoder.py)
- Z `decode_mapy_geometry` odstraním 20km fixní vzdálenostní limit (the "Absurd distance jump detected" check). 
- 2% cumulative tolerance drift přes `totalLength` pro vysoce detailní `dim` trasy zůstane plně v platnosti a zabrání degenerovaným FRPC datům.

### Regression Tests

#### [MODIFY] [test_decoder.py](file:///home/pepa/Desktop/vscoderepo/mapy-gpx-exporter/tests/test_decoder.py)
- Smažu starý dead kód z `test_decode_mapy_geometry`.

#### [MODIFY] [test_resolver.py](file:///home/pepa/Desktop/vscoderepo/mapy-gpx-exporter/tests/test_resolver.py)
- Přidám test pro `rg_chunks()` specificky s trasou `mukekodezu`, a přes `pytest.warns` (nebo absence varování) prokážu, že dekódování projde primární decode/encode cestou bez volání `warnings.warn` a fallback fallbacku.

#### [MODIFY] [test_client.py](file:///home/pepa/Desktop/vscoderepo/mapy-gpx-exporter/tests/test_client.py) (pokud existuje)
- Přidám test pro `fetch_many` proti asynchronnímu stahování alespoň jedné (namockované) `dim` trasy s `pyfrpc`.

## Verification Plan
1. `uv run pytest` musí ohlásit, že z repozitáře zcela zmizelo upozornění "decode/encode round-trip failed" u `mukekodezu`.
2. Otestuju funkčnost asynchronního commandline batch exportu (`mapy-gpx batch`) na mix `rc` a `dim` (pokud existuje live command, nebo aspoň v unit testech).
