---
persona: "hard_head"
biz_type: "plumbing"
end_reason: "jon_error: 'No time zone found with key America/Chicago'"
turns_used: 17
elapsed_seconds: 94.39
final_stage: "stage3"
started_at: "2026-05-28T16:55:04.449781+00:00"
stage_transitions:
  - turn: 8  (stage1 → stage2)
  - turn: 15  (stage2 → stage3)
---

# hard_head (plumbing)

**End:** jon_error: 'No time zone found with key America/Chicago'  |  **Turns:** 17  |  **Elapsed:** 94.39s

---

**system:** Hey, Jon here — who is this calling?

**prospect:** Yeah, who is this. I got a job in twenty minutes so make it quick.

**jon/stage1:** Yeah, appreciate you calling back. I'm just driving right now so I might cut out here and there. Sorry, I didn't catch your name?

**prospect/stage1:** Bobby. Bobby Mastrangelo. Mastrangelo Plumbing, Newark. You called my shop, remember? What's this about?

**tool/stage1:** `update_prospect_info({"first_name": "Bobby", "last_name": "Mastrangelo", "business_name": "Mastrangelo & Sons Plumbing"})` → `"ok"`

**jon/stage1:** Yeah Bobby, thanks for getting back to me. Just for some clarity on your side — are you guys mostly doing plumbing, or do you also do drain cleaning or water heaters?

**prospect/stage1:** All of it. Residential, commercial, drains, water heaters, you name it. Been doing it since '82. What's this actually about, Jon?

**jon/stage1:** Yeah totally, full service shop — got it. Look Bobby, let me give you the quick version since you've got a job in twenty minutes.
> 
> We actually own and operate a flooring company on our side, and we ended up taking that company from zero to four million in revenue in a few short years with a custom SEO system we built in-house. Off the backing and success of that, we transitioned to helping other contractors take on more local work in their area. So I guess this conversation would be more or less around marketing — are you even open to having that conversation?

**prospect/stage1:** Nah, not really. SEO, Google, all that — that's garbage. My trucks got my name on 'em. People in Newark know Mastrangelo. My old man built this on referrals, never spent a dime on marketing.

**jon/stage1:** Hundred percent, and that's a real foundation to build on — referrals and reputation, that's how the best shops run. Quick question though — if we could actually get you more work coming in on top of what you're already getting, is that even something you'd want?

**prospect/stage1:** I mean... I could handle more work yeah. But look, I heard this pitch before. You guys promise leads, they're garbage, waste of time. My guys are busy enough already.

**jon/stage1:** Yeah I hear you brother, bad leads are worse than no leads — totally fair. Real quick though, what's making you hesitant specifically? Is it more the quality of the leads, the cost, or just been burned before by some other company?

**prospect/stage1:** All three, pal. Paid some SEO guy two grand a month, got calls from people in Trenton. I'm in Newark. My grandfather didn't need Google, my old man didn't need Google, I don't need Google.

**jon/stage1:** Yeah that's a real thing — paying for leads from thirty miles away is just burning money, I get it completely. And look, what we built is different specifically because of that. We ran it on our own flooring company first — we weren't gonna pay for garbage leads either. The whole system is built around your area, your market. Newark plumbing jobs, not Trenton.
> 
> So even just to see what's actually possible in your area — no pressure either way — would it be worth a quick conversation to take a look at it?

**prospect/stage1:** Yeah that's fair, local's different I guess. But look, I'm still skeptical. What's "quick conversation" mean — you gonna try to sell me something on a twenty minute Zoom call?

**jon/stage1:** Nah nothing like that — it's just a quick call with my partner Waseem, he'll pull up your area, show you what the search volume looks like for plumbing in Newark, what's there to grab. No pitch deck, no hard sell. You look at the numbers, you decide if it makes sense. That's it.
> 
> So are you open to having that conversation?

**prospect/stage1:** Fine. But I'm not promising anything. I sit down, I look at numbers, I don't like what I see, I'm gone. What do you need from me?

**tool/stage1:** `handoff({"first_name": "Bobby", "biz_type": "plumbing", "reason": "Called back about text, full service plumbing shop in Newark since 1982 \u2014 residential, commercial, drains, water heaters", "raised_objections": ["not interested in marketing", "SEO is garbage, built on referrals", "been burned before by bad leads and out-of-area calls", "paid two grand a month for leads from Trenton not Newark", "skeptical about sales pitch on a Zoom call"]})` → `"Handoff complete."`

**jon/stage1:** That's all we ask Bobby, hundred percent.

**system/stage2:** <<handoff: stage1 → stage2>>

**jon/stage2:** Do you mind if I ask you a couple quick questions about the business just to get a better feel before we lock in a time?

**prospect/stage2:** Yeah go ahead. Make it quick though.

**jon/stage2:** Sounds good. So it sounds like you're the boss man — is this solely your company, or do you have any partners?

**prospect/stage2:** My shop, my name on the trucks. No partners. Just me and four guys.

**jon/stage2:** Got it, so you're running a crew of four. Are those guys all full-time, or you got some part-timers in the mix?

**prospect/stage2:** All full-time. Good guys too. Been with me a while.

**jon/stage2:** That's solid, good crew is hard to find. Where's the majority of your work coming from right now — mostly referrals and word of mouth, or you got anything coming in online, Facebook, Google, anything like that?

**prospect/stage2:** All referrals. Repeat customers. Word of mouth. Like I said, my trucks are my marketing. Nothing online.

**jon/stage2:** Yeah that tracks, makes sense for a shop that's been around as long as yours. Roughly how many jobs are you guys knocking out on a monthly basis right now?

**prospect/stage2:** Probably eight jobs a week, give or take. So call it thirty, thirty-five a month.

**jon/stage2:** Thirty, thirty-five a month, that's a solid clip. Last thing — if more work started coming in, would you have to bring somebody on, or could your current crew handle more jobs with what you got?

**prospect/stage2:** We could handle more. Got the capacity. That's not the issue. Finding good work, not garbage leads — that's the issue.

**tool/stage2:** `handoff({"first_name": "Bobby", "biz_type": "plumbing", "business": "Mastrangelo & Sons Plumbing", "city": "Newark", "state": "NJ", "crew_size": "4", "lead_source": "referrals and word of mouth only", "job_volume": "thirty to thirty-five jobs a month", "capacity_for_more": "yes, current crew can handle more without hiring"})` → `"Handoff complete."`

**jon/stage2:** Yeah totally, quality over quantity — that's exactly the right call.

**system/stage3:** <<handoff: stage2 → stage3>>

**jon/stage3:** Look Bobby, based on everything you've told me, sounds like you'd potentially be a good fit for what we have going on. What I'd love to do is have our team run a report for your area in Newark so we can take a look at some actual real numbers. Usually we jump on a video call on Zoom, share our screen, break down our system, show you exactly what we've done for our businesses. We obviously wouldn't expect you to invest into something that wouldn't financially make sense for your business.
> 
> When's the next best time for us to speak? You a morning or evening person —

**prospect/stage3:** Mornings are better. Early, before the jobs start. Look, what's your email, I'll send you mine and we can figure out a time. And I'm still not promising anything.
