import anthropic
import json
import os

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def suggest_and_select_topic() -> str:
    """
    Ask Claude to suggest 8 conspiracy/investigative video topics.
    Display them with hooks, let user pick one or enter a custom topic.
    Returns the chosen topic string.
    """
    print("\n" + "="*60)
    print("GENERATING TOPIC SUGGESTIONS...")
    print("="*60)

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": """Generate 8 compelling conspiracy/investigative YouTube video topics.
Return ONLY valid JSON array (no markdown, no backticks):
[{"title": "...", "hook": "One sentence teaser that creates intrigue"}, ...]

Requirements:
- Mix historical conspiracies, government cover-ups, unexplained events, secret programs
- Topics must have documented real-world evidence to investigate
- Suitable for a 7-10 minute investigative video with narration
- Hook should create intrigue without being clickbait
- Vary the eras and subjects (not all the same type)"""
        }]
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    topics = json.loads(raw)

    print("\n" + "="*60)
    print("TOPIC SUGGESTIONS")
    print("="*60)
    for i, t in enumerate(topics, 1):
        print(f"\n  {i}. {t['title']}")
        print(f"     {t['hook']}")
    print(f"\n  0. Enter your own topic")
    print("="*60)

    while True:
        choice = input("\nSelect a topic (0-8): ").strip()
        if choice == "0":
            custom = input("Enter your topic: ").strip()
            if custom:
                return custom
        elif choice.isdigit() and 1 <= int(choice) <= len(topics):
            return topics[int(choice) - 1]["title"]
        print("  Invalid choice, try again.")
