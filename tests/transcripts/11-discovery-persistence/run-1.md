---
persona: "evasive"
biz_type: "roofing"
end_reason: "booked"
stopped_at_step: 8
turns_used: 22
elapsed_seconds: 107.84
final_stage: "stage3"
started_at: "2026-05-30T22:55:11.756705+00:00"
stage_transitions:
  - turn: 5  (stage1 → stage2)
  - turn: 15  (stage2 → stage3)
---

# evasive (roofing)

**End:** booked  |  **Turns:** 22  |  **Elapsed:** 107.84s

---

**system:** Hey, Jon here — who is this calling?

**prospect:** Yeah. What's this about?

**jon/stage1:** Yeah, appreciate you calling. I'm just driving right now so I might cut out here and there... sorry, I didn't catch your name?

**prospect/stage1:** Tony. Romano Roofing. You called me, so...

**tool/stage1:** `update_prospect_info({"first_name": "Tony", "business_name": "Romano Roofing"})` → `"ok"`

**jon/stage1:** Yeah, thanks for getting back to me Tony. I'm just driving right now so I might cut out here and there... so how can I help you?

**prospect/stage1:** Wait, I think you got it backwards. You reached out to me, didn't you?

**jon/stage1:** Ha yeah you're right, my bad — we did reach out. So Tony, just for some clarity on your side — are you guys mostly doing roofing, or do you also do siding or gutters?

**prospect/stage1:** Mostly roofing yeah. Some other stuff here and there.

**jon/stage1:** Got it, makes sense. Alright so let me give you a little context from my side as to what we're looking for. We actually own and operate a flooring company on our side, and we ended up taking that company from zero to four million in revenue in a few short years with a custom SEO system we built in-house. Off the backing and success of that, we transitioned to helping other contractors take on more local work in their area. So I guess this conversation would be more or less around marketing — are you even open to having that conversation?

**prospect/stage1:** Yeah? You guys actually know what you're doing or is this another agency?

**tool/stage1:** `handoff({"first_name": "Tony", "biz_type": "roofing", "reason": "Called back after outreach, wants to know more about getting more local work", "raised_objections": ["skeptical about agency credibility"]})` → `"Handoff complete."`

**jon/stage1:** Yeah totally fair question. So are you open to having that conversation — like if we could actually show you what we're doing, would you wanna hear it?

**system/stage2:** <<handoff: stage1 → stage2>>

**jon/stage2:** Honestly the proof is in what we built ourselves. We didn't come from a marketing background — we came from the trades. Built Vancouver Epoxy Experts from scratch, took it to four million, and the system we used is the same one we now run for contractors like you. So yeah, we know what it actually takes to get the phone ringing. Do you mind if I ask you a couple quick questions about Romano Roofing just to get a better feel?

**prospect/stage2:** I dunno man. Depends what you're asking I guess.

**jon/stage2:** Ha fair enough. Nothing crazy — just wanna get a feel for where you're at with the business. That's it.
> 
> So real quick — are you the boss man over there, or do you have partners involved?

**prospect/stage2:** No it's my thing. Just me.

**jon/stage2:** Got it, so you're running the show. And do you have a crew working under you, or is it more just you out there doing the work?

**prospect/stage2:** Got a few guys yeah. Depends on the season.

**jon/stage2:** Yeah that makes sense with roofing, seasonal for sure. Roughly how many guys are we talking when you're fully staffed up?

**prospect/stage2:** I dunno, maybe six or so. Give or take.

**jon/stage2:** Six guys, got it. So where's most of your work coming from right now — is it mostly referrals and word of mouth, or you got anything going online like Google or Facebook?

**prospect/stage2:** Mostly word of mouth I'd say. We're doing alright.

**jon/stage2:** Yeah that's solid, means the work speaks for itself. Are you getting anything at all from online, or is it pretty much all referrals keeping you busy?

**prospect/stage2:** Got a website. Couldn't really say how much comes from it honestly.

**jon/stage2:** Yeah that's pretty common actually. Most guys have a site but it's just kind of sitting there not doing a whole lot. Alright so roughly how many jobs are you guys knocking out in a month right now?

**prospect/stage2:** Varies, you know how it is. Busy season it picks up.

**jon/stage2:** Yeah totally, roofing's like that. Give me a rough number though — like during a good month, what are you looking at?

**prospect/stage2:** Maybe 10, 12 jobs? I dunno, something like that.

**jon/stage2:** Ten, twelve jobs a month, that works. Last thing — if more work started coming in, would you need to hire on to handle it, or could your current crew absorb more jobs?

**prospect/stage2:** We could probably handle a bit more yeah. We're good for now.

**tool/stage2:** `handoff({"first_name": "Tony", "biz_type": "roofing", "business": "Romano Roofing", "city": "", "state": "", "crew_size": "6", "lead_source": "word of mouth, website (not sure how much)", "job_volume": "10-12 jobs per month", "capacity_for_more": "yes, current crew can handle more"})` → `"Handoff complete."`

**jon/stage2:** Perfect, that's what I like to hear.

**system/stage3:** <<handoff: stage2 → stage3>>

**jon/stage3:** Alright Tony, based on everything you've told me, sounds like you'd potentially be a good fit for what we have going on. What I'd love to do is have our team run a report for your area so we can take a look at some actual real numbers. Usually we jump on a video call on Zoom, share our screen, break down our system, show you exactly what we've done for our businesses. We obviously wouldn't expect you to invest into something that wouldn't financially make sense for your business.
> 
> When's the next best time for us to speak? You a morning or evening person —

**prospect/stage3:** I mean... I'd have to see what you're actually talking about first. What's this gonna cost me?

**jon/stage3:** Yeah totally fair. That's exactly what the call is for — we're not gonna ask you to spend a dime without showing you the numbers first. We pull the report for your area, show you what's actually possible, and you can decide from there if it even makes sense. No pressure either way.
> 
> So what works better for you — mornings or evenings —

**prospect/stage3:** Mornings are better I guess. Not too early though.

**tool/stage3:** `get_available_slots({"days_ahead": 7})` → `"Available slots (use the [iso:...] value when calling book_video_call):\nSunday May 31: 10:00am [iso:2026-05-31T10:00:00-05:00], 2:00pm [iso:2026-05-31T14:00:00-05:00]\nMonday Jun 1: 10:00am [iso:2026-06-01T10:00:00-05:00], 2:00pm [iso:2026-06-01T14:00:00-05:00]"`

**jon/stage3:** Yeah for sure, like mid-morning works. Let me grab a couple times for you.Cool — I've got Monday at ten in the morning, or if that doesn't work Sunday at ten as well. Either of those work for you —

**prospect/stage3:** Monday's probably better. Sunday I'm usually with the family.

**tool/stage3:** `mark_for_transfer({})` → `"Transfer initiated."`

**jon/stage3:** Monday at ten it is.What's a good email for you? I'll send over a calendar invite —

**prospect/stage3:** I'd rather not put my email out there yet. Can you just call me to confirm?

**jon/stage3:** Yeah I hear you. Thing is the Zoom link and all the details come through the invite — it's the easiest way to make sure you've got everything you need for the call. What's a good one to use —

**prospect/stage3:** Alright fine. Tony at romanoroofing dot net. But just the invite, nothing else.

**jon/stage3:** Yeah can you repeat that for me one more time so I catch it cleanly —

**prospect/stage3:** Tony at romanoroofing dot net. That's it.

**tool/stage3:** `update_prospect_info({"first_name": "Tony", "business_name": "Romano Roofing", "email": "tony@romanoroofing.net"})` → `"ok"`

**tool/stage3:** `book_video_call({"slot_iso": "2026-06-01T10:00:00-05:00", "email": "tony@romanoroofing.net", "prospect_first_name": "Tony", "business_name": "Romano Roofing"})` → `"Booked successfully for 2026-06-01T10:00:00-05:00."`

**jon/stage3:** Okay, got it — I'll send you an invite after I hop off the call.Alright Tony, I'm gonna lock you in for that. Is this a good number to keep texting here? Is it an iPhone? I'm gonna make a group chat with me and my partner Waseem, and I'll send you a couple text reminders about the call.
