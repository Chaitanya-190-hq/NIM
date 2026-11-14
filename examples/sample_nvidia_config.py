"""Sample NVIDIA NIM configuration you can paste into NVIDIA // BRIDGE.

Replace the api_key value with the real key from build.nvidia.com (it starts
with "nvapi-"). Nothing here is imported or executed by the backend — the
website only reads the string literals.

    base_url="https://integrate.api.nvidia.com/v1"
    api_key="nvapi-..."
    model="openai/gpt-oss-20b"
"""

from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="nvapi-YOUR_KEY_HERE",
)

completion = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=[{"role": "user", "content": "Hello"}],
    temperature=0.2,
    top_p=0.7,
    max_tokens=512,
)

print(completion.choices[0].message.content)
