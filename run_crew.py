"""Direct crew runner with auto-retry on rate limits."""
import sys
import io
import time

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from main import create_marketing_crew, reset_termination

TOPIC = "https://www.hexadata.in/resq i need a professional blog on this topic read the link carefully and give the content according to that"
OUTPUT_FILE = "final_campaign_result.md"

MAX_ATTEMPTS = 5

for attempt in range(1, MAX_ATTEMPTS + 1):
    print(f"\n{'='*60}")
    print(f"🚀 ATTEMPT {attempt}/{MAX_ATTEMPTS} - STARTING CREW RUN")
    print(f"📌 Topic: {TOPIC[:80]}...")
    print(f"{'='*60}\n")

    try:
        reset_termination()
        crew = create_marketing_crew()
        result = crew.kickoff(inputs={"topic": TOPIC})

        output_text = str(result)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("# Marketing Campaign Result\n\n")
            f.write(f"**Topic:** {TOPIC}\n\n")
            f.write("---\n\n")
            f.write(output_text)

        print(f"\n{'='*60}")
        print(f"✅ CREW RUN COMPLETE! (Attempt {attempt})")
        print(f"📄 Result saved to: {OUTPUT_FILE}")
        print(f"{'='*60}\n")
        print(output_text)
        sys.exit(0)

    except Exception as e:
        err = str(e)
        print(f"\n❌ ERROR on attempt {attempt}: {err[:300]}")

        if "rate_limit" in err.lower() or "rate limit" in err.lower():
            wait = 30 * attempt  # progressive backoff: 30s, 60s, 90s...
            print(f"⏳ Rate limit hit — waiting {wait}s before retry...")
            time.sleep(wait)
            continue
        elif "decommissioned" in err.lower() or "not found" in err.lower():
            print("❌ Model decommissioned - please update model name in main.py")
            sys.exit(1)
        else:
            # Unknown error - wait a bit and retry
            print(f"⏳ Unknown error — waiting 15s before retry...")
            time.sleep(15)
            continue

print("\n❌ All attempts exhausted. Check errors above.")
sys.exit(1)
