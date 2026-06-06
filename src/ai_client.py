import streamlit as st


def get_ai_response(messages, system_prompt=None, max_tokens=1024):
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

        kwargs = {
            "model": "claude-sonnet-4-6",
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_prompt is not None:
            kwargs["system"] = system_prompt

        response = client.messages.create(**kwargs)
        return response.content[0].text
    except Exception as e:
        return f"Error calling AI: {e}"
