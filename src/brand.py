import streamlit as st

# Google Fonts loaded via @import inside the <style> block.
# <link> elements can be stripped by Streamlit's HTML sanitizer; a single
# <style> block is the reliable injection path for global CSS.
_HAMLET_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,500;8..60,600&family=Inter:wght@400;500;600&display=swap');

/* Base typography — page background is Bone-2 (#EFEBE0), cards lift to Bone (#F4F1EA) */
html, body, .stApp, .main {
    font-family: 'Inter', system-ui, sans-serif !important;
    background-color: #EFEBE0 !important;
}
h1, h2, h3, h4,
.stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
    font-family: 'Source Serif 4', Georgia, serif !important;
    font-weight: 500 !important;
    color: #232323;
}

/* Buttons */
.stButton > button {
    background-color: #1F4788 !important;
    color: #F4F1EA !important;
    border: none !important;
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    padding: 0.45rem 1.1rem !important;
    transition: background-color 0.15s ease;
}
.stButton > button:hover {
    background-color: #163562 !important;
    color: #F4F1EA !important;
}
.stButton > button:focus {
    outline: 2px solid #1F4788 !important;
    outline-offset: 2px !important;
}

/* Inputs and text areas */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stSelectbox > div > div,
.stNumberInput > div > div > input {
    border-radius: 8px !important;
    border-color: #E0DACD !important;
    font-family: 'Inter', sans-serif !important;
    background-color: #F4F1EA !important;
}
.stTextInput > div > div > input:focus,
.stTextArea > div > div > textarea:focus {
    border-color: #1F4788 !important;
    box-shadow: 0 0 0 2px rgba(31,71,136,0.15) !important;
}

/* Expanders — card surface (#F4F1EA) lifts off page (#EFEBE0) */
.streamlit-expanderHeader {
    font-family: 'Inter', sans-serif !important;
    background-color: #F4F1EA !important;
    border-radius: 8px !important;
    border: 1px solid #E0DACD !important;
}
.streamlit-expanderContent {
    border: 1px solid #E0DACD !important;
    border-top: none !important;
    border-radius: 0 0 8px 8px !important;
    background-color: #F4F1EA !important;
}
div[data-testid="stExpander"] {
    border-radius: 8px !important;
    border: 1px solid #E0DACD !important;
    overflow: hidden !important;
    background-color: #F4F1EA !important;
    box-shadow: 0 1px 3px rgba(35,35,35,0.06) !important;
}

/* Sidebar — card surface, slightly lighter than page */
section[data-testid="stSidebar"] {
    background-color: #F4F1EA !important;
    border-right: 1px solid #E0DACD !important;
}

/* Captions and muted text */
.stCaption, small, .caption {
    color: #8A8579 !important;
    font-family: 'Inter', sans-serif !important;
}

/* Tables — card surface with soft shadow */
.stTable table {
    border-collapse: collapse !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 0.875rem !important;
    background-color: #F4F1EA !important;
    box-shadow: 0 1px 3px rgba(35,35,35,0.06) !important;
}
.stTable table th {
    background-color: #EFEBE0 !important;
    color: #232323 !important;
    font-weight: 600 !important;
    border-bottom: 1px solid #E0DACD !important;
}
.stTable table td {
    border-bottom: 1px solid #E0DACD !important;
    color: #232323 !important;
}

/* Notification boxes — brand rounding only, keep Streamlit semantic colours */
div[data-baseweb="notification"] {
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
}

/* Divider hairline */
hr {
    border-color: #E0DACD !important;
    border-width: 1px 0 0 0 !important;
}

/* Hide Streamlit's fixed top bar — it sits above the content and clips
   the brand logo once our padding-top: 1rem override is active. */
header[data-testid="stHeader"] {
    display: none !important;
}

/* Hide Streamlit chrome for presentation */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }

/* Score breakdown container */
div[data-testid="stExpander"] {
    border-radius: 8px !important;
    border: 1px solid #E0DACD !important;
    overflow: hidden !important;
}

/* Spinner */
.stSpinner > div {
    border-top-color: #1F4788 !important;
}

/* Metric widgets — card surface with soft shadow */
[data-testid="metric-container"] {
    background-color: #F4F1EA !important;
    border: 1px solid #E0DACD !important;
    border-radius: 16px !important;
    padding: 1rem !important;
    box-shadow: 0 1px 3px rgba(35,35,35,0.06) !important;
}

/* Code / JSON blocks — card surface */
.stCodeBlock, pre, code {
    font-family: 'JetBrains Mono', 'Fira Code', monospace !important;
    background-color: #F4F1EA !important;
    border: 1px solid #E0DACD !important;
    border-radius: 8px !important;
    color: #232323 !important;
}

/* Tabs — card surface for tab strip */
.stTabs [data-baseweb="tab-list"] {
    background-color: #F4F1EA !important;
    border-radius: 8px !important;
    padding: 4px !important;
    border: 1px solid #E0DACD !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 4px !important;
    font-family: 'Inter', sans-serif !important;
}
.stTabs [aria-selected="true"] {
    background-color: #1F4788 !important;
    color: #F4F1EA !important;
}

/* Main content block padding */
.block-container {
    padding-top: 1rem !important;
}
</style>
"""

# Brand header HTML
_HAMLET_HEADER_HTML = """
<div style="
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 0 16px 0;
  border-bottom: 1px solid #E0DACD;
  margin-bottom: 24px;
">
  <svg viewBox="-66 -66 132 132" width="52" height="52"
       role="img" aria-label="Hamlet mark"
       style="flex-shrink:0; display:block; border-radius:12px;">
    <!-- App-icon background -->
    <rect x="-62" y="-62" width="124" height="124" rx="24" fill="#1F4788"/>
    <!-- 8 rounded rects in cream -->
    <g fill="#F4F1EA">
      <rect x="-7"    y="-53"   width="14" height="14" rx="3"/>
      <rect x="25.5"  y="-39.5" width="14" height="14" rx="3"/>
      <rect x="39"    y="-7"    width="14" height="14" rx="3"/>
      <rect x="25.5"  y="25.5"  width="14" height="14" rx="3"/>
      <rect x="-7"    y="39"    width="14" height="14" rx="3"/>
      <rect x="-39.5" y="25.5"  width="14" height="14" rx="3"/>
      <rect x="-53"   y="-7"    width="14" height="14" rx="3"/>
      <rect x="-39.5" y="-39.5" width="14" height="14" rx="3"/>
    </g>
    <circle r="21" fill="#C2603F"/>
  </svg>
  <div>
    <div style="
      font-family: 'Source Serif 4', Georgia, serif;
      font-size: 1.75rem;
      font-weight: 500;
      color: #1F4788;
      line-height: 1.15;
    ">Hamlet</div>
    <div style="
      font-family: 'Inter', system-ui, sans-serif;
      font-size: 0.875rem;
      color: #8A8579;
      margin-top: 2px;
    ">Layouts planned around people</div>
  </div>
</div>
"""


def apply_brand() -> None:
    """Inject Hamlet CSS and fonts. Call once per render, after st.set_page_config."""
    st.markdown(_HAMLET_CSS, unsafe_allow_html=True)


def render_brand_header() -> None:
    """Render the Hamlet logomark + wordmark + tagline at the top of the page."""
    st.markdown(_HAMLET_HEADER_HTML, unsafe_allow_html=True)
