import streamlit as st
import pandas as pd
from geopy.distance import geodesic
from geopy.geocoders import Nominatim
import pgeocode
import folium
from streamlit_folium import st_folium
import requests
from bs4 import BeautifulSoup
import json
import re
from urllib.parse import quote_plus
from urllib.parse import urlparse, parse_qs, unquote
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

st.set_page_config(page_title="LineDance Tracker", layout="wide", page_icon="🕺")

# Custom CSS - modern line-dance theme + centered headers
st.markdown("""
<style>
    .main {background-color: #f8f1e3;}
    .stApp h1 {color: #d97706; font-family: 'Georgia', serif;}
    .stApp h2, .stApp h3 {color: #b45309;}
    .stExpander {border: 1px solid #fed7aa;}
    .stButton>button {background-color: #b45309; color: white;}
    
    /* Center table headers */
    .stTable th, table th {
        text-align: center !important;
        background-color: #d97706;
        color: white;
        padding: 10px;
    }
    .stTable td {
        vertical-align: middle;
    }
    a {color: #b45309; font-weight: bold;}
</style>
""", unsafe_allow_html=True)

# Sidebar controls
with st.sidebar:
    st.title("🕺 LineDance Tracker")
    user_zip = st.text_input("ZIP Code", value="22508", key="user_zip")
    max_drive = st.slider("Maximum driving distance (miles)", 10, 150, 50, key="max_drive")
    include_fallback = st.checkbox("Include manual fallback events", value=True)
    if st.button("🔄 Refresh web sources"):
        st.cache_data.clear()
        st.rerun()

# Force full refresh when ZIP code changes
if "previous_zip" not in st.session_state:
    st.session_state.previous_zip = user_zip
if st.session_state.previous_zip != user_zip:
    st.cache_data.clear()
    st.session_state.previous_zip = user_zip
    st.rerun()

# Updated title - "Real" removed
st.markdown("**Line Dancing Near Fredericksburg / Spotsylvania** 🎶")

# Load geocoding
if 'nomi' not in st.session_state:
    try:
        st.session_state.nomi = pgeocode.Nominatim('us')
    except:
        st.error("Run: `pip install pgeocode folium streamlit-folium`")
        st.stop()

nomi = st.session_state.nomi

def _address_to_zip(address: str) -> str:
    match = re.search(r"\b\d{5}(?:-\d{4})?\b", address or "")
    return match.group(0)[:5] if match else ""


def _zip_search_context(zip_code: str):
    try:
        info = nomi.query_postal_code(zip_code)
        city = getattr(info, "place_name", "") or ""
        state = getattr(info, "state_code", "") or ""
        return city.strip(), state.strip()
    except Exception:
        return "", ""


def _build_search_queries(zip_code: str, radius_miles: int):
    city, state = _zip_search_context(zip_code)
    area_terms = " ".join([term for term in [city, state, zip_code] if term]).strip()
    radius_phrase = f"within {radius_miles} miles"
    queries = [
        f"\"line dancing\" OR \"line dance\" OR \"soul line dance\" events {radius_phrase} of {area_terms} -python -stackoverflow",
        f"\"soul line dance\" OR \"country line dance\" classes {radius_phrase} of {area_terms}",
        f"\"line dance\" nights OR class OR lesson {radius_phrase} {area_terms}",
        f"\"line dance\" OR \"line dancing\" (Cheri OR Regina OR \"Olivia Ray\" OR \"Boom Fitness\" OR \"Linda\" OR \"Sheila Snipes\") Virginia OR Fredericksburg OR Spotsylvania",
    ]
    if radius_miles > 80:
        queries.extend([
            f"\"line dancing\" OR \"line dance\" OR \"soul line dance\" {area_terms} OR Virginia",
            f"\"country line dance\" OR \"soul line dance\" class OR night Virginia",
        ])
    return queries


def build_sources_for_zip_radius(zip_code: str, radius_miles: int):
    city, state = _zip_search_context(zip_code)
    city_slug = (city or zip_code).lower().replace(" ", "-")
    state_slug = (state or "va").lower()
    search_phrase = quote_plus(f"line dancing {city} {state} within {radius_miles} miles".strip())
    location_phrase = quote_plus(f"{city}, {state}".strip(", "))

    return [
        {"name": f"Eventbrite {city or zip_code}", "url": f"https://www.eventbrite.com/d/{state_slug}--{city_slug}/line-dancing/"},
        {"name": f"Meetup {city or zip_code}", "url": f"https://www.meetup.com/find/?keywords=line+dancing&location={location_phrase}&source=EVENTS"},
        {"name": f"AllEvents {city or zip_code}", "url": f"https://allevents.in/search?keyword=line+dancing&city={quote_plus(city or zip_code)}"},
        {"name": f"Facebook Events {city or zip_code}", "url": f"https://www.facebook.com/events/search/?q={search_phrase}"},
        {"name": f"Google Events query {city or zip_code}", "url": f"https://www.google.com/search?q={search_phrase}+events"},
        {"name": "World Line Dance Newsletter (VA)", "url": "https://www.worldlinedancenewsletter.com/wtd/virginia.html"},
        {"name": "Gotta Line Dance VA", "url": "https://www.gottalinedanceva.com/"},
        {"name": "Line Dance Cheri", "url": "https://www.linedancecheri.us/"},
        {
            "name": "Stockyard Fredericksburg",
            "url": "https://www.stockyardsva.com/",
            "address": "409 William Street, Fredericksburg, VA 22401",
            "lat": 38.3032,
            "lon": -77.4605,
            "keywords": ["line dance", "line dancing", "whiskey wednesday"]
        },
    ]


def _is_line_dancing_event(event_name: str) -> bool:
    if not event_name:
        return False
    name_lower = event_name.lower()
    keywords = [
        "line dance", "line dancing", "linedance", "line-dance",
        "soul line dance", "soul linedance", "country line dance",
        "line dance class", "line dance night", "line dancing with",
        "instructional line dance", "line dance lesson"
    ]
    return any(kw in name_lower for kw in keywords)


@st.cache_data(ttl=3600)
def geocode_address(address: str):
    if not address:
        return None, None
    zip_code = _address_to_zip(address)
    if zip_code:
        loc = nomi.query_postal_code(zip_code)
        if not pd.isna(loc.latitude) and not pd.isna(loc.longitude):
            return float(loc.latitude), float(loc.longitude)

    if "geocoder" not in st.session_state:
        st.session_state.geocoder = Nominatim(user_agent="line_dance_tracker_app")
    try:
        place = st.session_state.geocoder.geocode(address, timeout=8)
        if place:
            return float(place.latitude), float(place.longitude)
    except Exception:
        pass
    return None, None


def _extract_ldjson_events(node):
    events = []
    if isinstance(node, dict):
        node_type = node.get("@type")
        if node_type == "Event" or (isinstance(node_type, list) and "Event" in node_type):
            events.append(node)
        for value in node.values():
            events.extend(_extract_ldjson_events(value))
    elif isinstance(node, list):
        for item in node:
            events.extend(_extract_ldjson_events(item))
    return events


def _event_location_text(event_node: dict) -> str:
    loc = event_node.get("location", {})
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, dict):
        address = loc.get("address", "")
        name = loc.get("name", "")
        if isinstance(address, dict):
            parts = [
                address.get("streetAddress", ""),
                address.get("addressLocality", ""),
                address.get("addressRegion", ""),
                address.get("postalCode", "")
            ]
            address = ", ".join([p for p in parts if p])
        return ", ".join([p for p in [name, address] if p]).strip(", ")
    return str(loc) if loc else ""


def _event_coordinates(event_node: dict):
    location = event_node.get("location", {})
    if isinstance(location, list) and location:
        location = location[0]

    candidates = []
    if isinstance(location, dict):
        candidates.append(location.get("geo"))
        address_obj = location.get("address")
        if isinstance(address_obj, dict):
            candidates.append(address_obj.get("geo"))
    candidates.append(event_node.get("geo"))

    for geo in candidates:
        if not isinstance(geo, dict):
            continue
        lat = geo.get("latitude")
        lon = geo.get("longitude")
        try:
            if lat is not None and lon is not None:
                return float(lat), float(lon)
        except (TypeError, ValueError):
            continue

    return None, None


@st.cache_data(ttl=3600)
def fetch_web_events(source_list, max_drive, user_zip):
    headers = {"User-Agent": "LineDanceTracker/1.0"}
    collected_events = []
    source_health = []
    total_parsed = 0
    filtered_out = 0

    for source in source_list:
        source_name = source["name"]
        source_url = source["url"]
        source_address = source.get("address", "")
        source_lat = source.get("lat")
        source_lon = source.get("lon")
        source_keywords = [k.lower() for k in source.get("keywords", [])]
        parsed_count = 0
        status = "No events"
        error_msg = ""

        try:
            if "stockyardsva.com" in source_url.lower():
                session = requests.Session()
                retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
                adapter = HTTPAdapter(max_retries=retry_strategy)
                session.mount("https://", adapter)
                resp = session.get(source_url, headers=headers, timeout=15, verify=False)
            else:
                resp = requests.get(source_url, headers=headers, timeout=12)

            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            page_text_lower = soup.get_text(" ", strip=True).lower()
            scripts = soup.find_all("script", attrs={"type": "application/ld+json"})

            for script in scripts:
                raw_json = (script.string or script.text or "").strip()
                if not raw_json:
                    continue
                try:
                    parsed_json = json.loads(raw_json)
                except json.JSONDecodeError:
                    continue

                events = _extract_ldjson_events(parsed_json)
                for event in events:
                    total_parsed += 1
                    event_name = event.get("name", "Untitled Event")
                    if not _is_line_dancing_event(event_name):
                        filtered_out += 1
                        continue

                    address = _event_location_text(event).strip()
                    lat, lon = _event_coordinates(event)

                    if lat is None or lon is None:
                        lat, lon = geocode_address(address)
                    if lat is None or lon is None:
                        continue

                    event_link = event.get("url") or source_url
                    start_time = event.get("startDate", "See source page")

                    collected_events.append({
                        "Event/Venue": event_name,
                        "Address": address or "Address not provided (see source page)",
                        "Lat": lat,
                        "Lon": lon,
                        "Date/Time": str(start_time),
                        "Link": event_link,
                        "Source": source_name
                    })
                    parsed_count += 1

            if parsed_count == 0 and source_keywords and any(k in page_text_lower for k in source_keywords):
                fallback_lat = source_lat
                fallback_lon = source_lon
                if (fallback_lat is None or fallback_lon is None) and source_address:
                    fallback_lat, fallback_lon = geocode_address(source_address)

                if fallback_lat is not None and fallback_lon is not None:
                    collected_events.append({
                        "Event/Venue": f"Line dancing at {source_name}",
                        "Address": source_address or "See venue page for location",
                        "Lat": float(fallback_lat),
                        "Lon": float(fallback_lon),
                        "Date/Time": "See venue page",
                        "Link": source_url,
                        "Source": source_name
                    })
                    parsed_count += 1

            if parsed_count > 0:
                status = "OK"
        except Exception as exc:
            status = "Error"
            error_msg = str(exc)[:120]

        source_health.append({
            "Source": source_name,
            "Status": status,
            "Events Found": parsed_count,
            "URL": source_url,
            "Details": error_msg
        })

    return pd.DataFrame(collected_events), pd.DataFrame(source_health)


@st.cache_data(ttl=3600)
def discover_sources_duckduckgo(user_zip_code: str, radius_miles: int, max_results: int = 12):
    headers = {"User-Agent": "LineDanceTracker/1.0"}
    discovered = []
    seen_urls = set()
    search_queries = _build_search_queries(user_zip_code, radius_miles)

    def _normalize_result_url(href: str) -> str:
        if not href:
            return ""
        href = href.strip()
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("/"):
            href = "https://duckduckgo.com" + href

        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc:
            query = parse_qs(parsed.query)
            if "uddg" in query and query["uddg"]:
                return unquote(query["uddg"][0])
            if parsed.path.startswith("/l/") and "rut" in query and query["rut"]:
                return unquote(query["rut"][0])
            return ""
        return href

    candidate_selectors = [
        "a.result__a",
        "a.result-link",
        "h2 a",
        "a[data-testid='result-title-a']",
    ]

    for query in search_queries:
        for endpoint in ["https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"]:
            try:
                url = f"{endpoint}?q={quote_plus(query)}"
                resp = requests.get(url, headers=headers, timeout=12)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

                links = []
                for selector in candidate_selectors:
                    links.extend(soup.select(selector))

                seen_hrefs = set()
                unique_links = []
                for link in links:
                    raw_href = (link.get("href") or "").strip()
                    if raw_href and raw_href not in seen_hrefs:
                        seen_hrefs.add(raw_href)
                        unique_links.append(link)

                for link in unique_links:
                    href = _normalize_result_url(link.get("href") or "")
                    label = link.get_text(strip=True) or "Web result"
                    if not href or href in seen_urls:
                        continue
                    if any(bad in href for bad in ["javascript:void", "#"]):
                        continue
                    seen_urls.add(href)
                    discovered.append({"name": label[:80], "url": href})
                    if len(discovered) >= max_results:
                        return discovered
            except Exception:
                continue

    return discovered


@st.cache_data(ttl=3600)
def discover_sources_bing(user_zip_code: str, radius_miles: int, max_results: int = 12):
    headers = {"User-Agent": "LineDanceTracker/1.0"}
    discovered = []
    seen_urls = set()
    queries = _build_search_queries(user_zip_code, radius_miles)

    for query in queries:
        try:
            rss_url = f"https://www.bing.com/search?q={quote_plus(query)}&format=rss"
            resp = requests.get(rss_url, headers=headers, timeout=12)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "xml")
            for item in soup.find_all("item"):
                link = (item.find("link").text or "").strip() if item.find("link") else ""
                title = (item.find("title").text or "").strip() if item.find("title") else "Bing result"
                if not link or link in seen_urls:
                    continue
                if any(bad in link for bad in ["bing.com", "microsoft.com"]):
                    continue
                seen_urls.add(link)
                discovered.append({"name": title[:80], "url": link})
                if len(discovered) >= max_results:
                    return discovered
        except Exception:
            continue

    return discovered


fallback_events = pd.DataFrame({
    "Event/Venue": [
        "Whiskey Wednesdays with Line Dance Cheri at Stockyards",
        "Free Line Dancing with Dancing with Regina at Orleans Bistro",
        "Line Dancing with Boom Fitness (Karen's) at Wilderness Presidential Resort",
        "Virginia Line Dance Festival 2026",
        "Soul Line Dancing with Regina at Jay's Sports Lounge"
    ],
    "Address": [
        "409 William Street, Fredericksburg, VA 22401",
        "5442 Southpoint Plaza Way, Fredericksburg, VA 22407",
        "9220 Plank Road, Spotsylvania, VA 22553",
        "Holiday Inn Fredericksburg Conference Center, Fredericksburg, VA",
        "409 William Street, Fredericksburg, VA 22401"
    ],
    "Lat": [38.3032, 38.1970, 38.1250, 38.3100, 38.2985],
    "Lon": [-77.4605, -77.5000, -77.7250, -77.4605, -77.4625],
    "Date/Time": [
        "Every Wednesday 7:00 PM – 10:00 PM",
        "Every Wednesday 7:00 PM – 9:00 PM",
        "Selected Fridays 6:30 PM – 8:30 PM",
        "July 30 – August 2, 2026",
        "Every 2nd & 4th Thursday 7:00 PM – 10:00 PM"
    ],
    "Link": [
        "https://www.linedancecheri.us/",
        "https://www.dancingwithregina.com/upcoming-classes",
        "https://wpresort.com/events/line-dancing-with-boom-fitness/",
        "https://virginialinedancefestival.com/",
        "https://www.dancingwithregina.com/"
    ],
    "Source": ["Manual fallback"] * 5
})

manual_sources = [
    {"name": "Line Dance Cheri", "url": "https://www.linedancecheri.us/"},
    {"name": "Dancing with Regina", "url": "https://www.dancingwithregina.com/upcoming-classes"},
    {"name": "Virginia Line Dance Festival", "url": "https://virginialinedancefestival.com/"},
    {"name": "Wilderness Presidential Resort", "url": "https://wpresort.com/events/line-dancing-with-boom-fitness/"}
]
max_search_sources = 30

seed_sources = build_sources_for_zip_radius(user_zip, max_drive)

discovery_max = 20 if max_drive > 50 else 8
discovered = discover_sources_duckduckgo(user_zip, max_drive, max_results=discovery_max)
discovered += discover_sources_bing(user_zip, max_drive, max_results=discovery_max)

auto_sources = seed_sources + discovered + manual_sources
seen_urls = set()
deduped_sources = []
for src in auto_sources:
    if src["url"] in seen_urls:
        continue
    seen_urls.add(src["url"])
    deduped_sources.append(src)
sources = deduped_sources[:max_search_sources]

web_events, source_status = fetch_web_events(sources, max_drive, user_zip)

if include_fallback:
    fallback_web_events, fallback_status = fetch_web_events(manual_sources, max_drive, user_zip)
    source_status = pd.concat([source_status, fallback_status], ignore_index=True)
    events = pd.concat([web_events, fallback_web_events, fallback_events], ignore_index=True).drop_duplicates(
        subset=["Event/Venue", "Address", "Date/Time"]
    )
else:
    events = web_events.copy()

events = events[events["Event/Venue"].apply(_is_line_dancing_event)].copy()

required_event_columns = [
    "Event/Venue", "Address", "Lat", "Lon", "Date/Time", "Link", "Source"
]
events = events.reindex(columns=required_event_columns)

required_source_columns = ["Source", "Status", "Events Found", "URL", "Details"]
source_status = source_status.reindex(columns=required_source_columns).fillna("")

# Get user coordinates from ZIP
try:
    loc = nomi.query_postal_code(user_zip)
    if pd.isna(loc.latitude) or pd.isna(loc.longitude):
        user_lat, user_lon = 38.317, -77.79
    else:
        user_lat = float(loc.latitude)
        user_lon = float(loc.longitude)
except:
    user_lat, user_lon = 38.317, -77.79

# Calculate distances
if events.empty:
    events["Distance (miles)"] = pd.Series(dtype=float)
else:
    events["Lat"] = pd.to_numeric(events["Lat"], errors="coerce")
    events["Lon"] = pd.to_numeric(events["Lon"], errors="coerce")
    events = events.dropna(subset=["Lat", "Lon"]).copy()

    distances = [
        round(geodesic((user_lat, user_lon), (float(lat), float(lon))).miles, 1)
        for lat, lon in zip(events["Lat"], events["Lon"])
    ]
    events["Distance (miles)"] = distances

# Sort by closest distance
filtered = events[events["Distance (miles)"] <= max_drive].copy()
filtered = filtered.sort_values(by="Distance (miles)").copy()

# Tabs
tab_list, tab_map, tab_log = st.tabs(["🔍 Events List", "🗺️ Map", "📊 My Dance Log"])

with tab_list:
    st.subheader(f"Events within {max_drive} miles of ZIP **{user_zip}**")
    max_in_dataset = events["Distance (miles)"].max() if not events.empty else 0
    st.caption(f"Showing {len(filtered)} line dancing events • Max distance found: **{max_in_dataset:.1f} miles**")
    st.info("📍 Line dancing events are mostly local. Larger radii may not add many more results.")

    if filtered.empty:
        st.warning("No events found in that range.")
    else:
        filtered_display = filtered.copy()
        filtered_display["Event/Venue"] = filtered_display.apply(
            lambda r: f'<a href="{r["Link"]}" target="_blank">{r["Event/Venue"]}</a>', axis=1)
        st.write(
            filtered_display[["Event/Venue", "Address", "Distance (miles)", "Date/Time"]]
            .to_html(escape=False, index=False),
            unsafe_allow_html=True
        )

    with st.expander("📋 Source coverage"):
        st.dataframe(source_status[["Source", "Status", "Events Found", "URL", "Details"]], use_container_width=True, hide_index=True)
    with st.expander("🌐 Discovered websites this run"):
        if sources:
            st.dataframe(pd.DataFrame(sources), use_container_width=True, hide_index=True)
        else:
            st.write("No websites discovered this run.")

with tab_map:
    st.subheader(f"Map of Events within {max_drive} miles of ZIP **{user_zip}**")
    zoom_level = max(6, 14 - int(max_drive / 20))
    m = folium.Map(location=[user_lat, user_lon], zoom_start=zoom_level)
    folium.Marker([user_lat, user_lon], popup="You are here", icon=folium.Icon(color="blue")).add_to(m)
    for _, row in filtered.iterrows():
        popup_html = f"""
        <b>{row['Event/Venue']}</b><br>
        📍 {row['Address']}<br>
        🕒 {row['Date/Time']}<br>
        📏 {row['Distance (miles)']} miles away<br><br>
        <a href="{row['Link']}" target="_blank">🔗 Visit Event Page</a>
        """
        folium.Marker([row['Lat'], row['Lon']], popup=folium.Popup(popup_html, max_width=350), icon=folium.Icon(color="red", icon="music")).add_to(m)
    st_folium(m, width="100%", height=600)

with tab_log:
    st.header("📝 Track Your Dancing")
    dance_name = st.text_input("Dance or Event You Attended", placeholder="e.g. Whiskey Wednesday at Stockyards")
    if st.button("✅ Log This Dance", type="primary"):
        if dance_name.strip():
            if 'dance_log' not in st.session_state:
                st.session_state.dance_log = []
            st.session_state.dance_log.append(dance_name.strip())
            st.success(f"🎉 Logged: **{dance_name}**")
            st.balloons()
    if 'dance_log' in st.session_state and st.session_state.dance_log:
        st.subheader("Your Recent Dances")
        for i, dance in enumerate(reversed(st.session_state.dance_log[-15:]), 1):
            st.markdown(f"**{i}.** {dance}")

st.caption("LineDance Tracker | Title updated • Drive Time removed • Sorted by closest • Headers centered! 🚀")