# Refugee Camp Layout Generator

A Streamlit web application for generating and evaluating refugee camp layouts.

## Setup

```bash
# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Configure secrets
copy .streamlit\secrets.toml.example .streamlit\secrets.toml
# Edit .streamlit/secrets.toml and replace "your-key-here" with your Anthropic API key

# Run the app
streamlit run app.py
```
