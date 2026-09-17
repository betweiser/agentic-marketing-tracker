#!/usr/bin/env python3
"""
Agentic AI Marketing Tracker — syncs the Metabase SQL query into a Google Sheet.

What it does, every time it runs:
 1. Reads Month / Year / Start Date / End Date from the "Dashboard" tab (cells B1:B4).
 2. Turns those into a from_date / to_date range.
 3. Sends the full SQL query (embedded below) to Metabase's /api/dataset endpoint
    with that date range as parameters.
 4. Writes the result table back into the sheet, formatted, replacing old data.

We send the raw SQL directly (instead of calling the saved card) because the
API key in use doesn't have permission to read the card's native query text
back from Metabase — sending the SQL ourselves sidesteps that.

Needs these environment variables (set as GitHub Secrets, injected by the workflow):
  METABASE_API_KEY          - your Metabase API key
  METABASE_BASE_URL         - e.g. https://metabase-lierhfgoeiwhr.newtonschool.co
  METABASE_DATABASE_ID      - 29 (Altius)
  GOOGLE_CREDENTIALS_FILE   - path to the service-account json (written by the workflow)
  SHEET_ID                  - the Google Sheet's ID (from its URL)
  SHEET_TAB_NAME            - default "Dashboard"
"""

import os
import sys
import calendar
import datetime as dt

import requests
import gspread
from google.oauth2.service_account import Credentials

MB_BASE_URL = os.environ["METABASE_BASE_URL"].rstrip("/")
MB_API_KEY = os.environ["METABASE_API_KEY"]
MB_DATABASE_ID = int(os.environ.get("METABASE_DATABASE_ID", "29"))
GOOGLE_CREDENTIALS_FILE = os.environ["GOOGLE_CREDENTIALS_FILE"]
SHEET_ID = os.environ["SHEET_ID"]
SHEET_TAB_NAME = os.environ.get("SHEET_TAB_NAME", "Dashboard")

HEADER_ROW = 6          # row the column headers live on
DATA_START_ROW = 7      # first data row

MONTHS = {name.lower(): num for num, name in enumerate(calendar.month_name) if name}

# The full query, exactly as used in the saved Metabase card (#11715).
SQL_QUERY = r"""
WITH
params AS (
    SELECT
        {{from_date}}::date AS start_date,
        {{to_date}}::date   AS end_date,
        DATE_TRUNC('month', {{from_date}}::date) AS range_month
),
alias_map (utm_medium_variant, correct_ad_name) AS (
    VALUES
    ('agentic-ai-gen-static-pm-rolesupby-400-18-06-26-pm1',
     'Agentic-AI-Gen-PM-rolesupby-400%-18-06-26-PM1'),
    ('agenticai-job-titles-sde-aiengineers-new-sde',
     'AgenticAI-job-titles-sde-AIengineers-now-SDE')
),
ad_metrics_raw AS (
    SELECT
        ad_id,
        ad_name,
        ad_group_id,
        ad_group_name,
        raw_payload ->> 'campaign_id'   AS campaign_id,
        campaign_name,
        platform,
        date,
        cost,
        impressions,
        inline_link_clicks,
        outbound_clicks
    FROM advertising_metrics_daily
    WHERE platform = 'Meta Ads'
      AND campaign_name IN (
          'Agentic AI Course',
          'Agentic-AI-Course-Retargeting',
          'Agentic AI Generalist',
          'Agentic-AI-Generalist',
          'Agentic-AI-Job-Titles-AdSet2',
          'Agentic-AI-sde-sep'
      )
      AND date >= (SELECT start_date FROM params)
      AND date <= (SELECT end_date FROM params)
),
ad_metrics AS (
    SELECT
        ad_id,
        MIN(ad_name)        AS ad_name,
        MIN(ad_group_id)    AS ad_group_id,
        MIN(ad_group_name)  AS ad_group_name,
        MIN(campaign_id)    AS campaign_id,
        MIN(campaign_name)  AS campaign_name,
        MIN(platform)       AS platform,
        SUM(impressions)              AS impressions,
        COALESCE(SUM(outbound_clicks), 0) AS link_clicks,
        SUM(cost)                     AS spend,
        TRIM(LOWER(REGEXP_REPLACE(MIN(ad_name), '[-_\s]+', ' ', 'g'))) AS norm_ad
    FROM ad_metrics_raw
    GROUP BY ad_id
),
signups_raw AS (
    SELECT
        COALESCE(
            (SELECT TRIM(LOWER(REGEXP_REPLACE(am.correct_ad_name, '[-_\s]+', ' ', 'g')))
             FROM alias_map am
             WHERE TRIM(LOWER(REGEXP_REPLACE(am.utm_medium_variant, '[-_\s]+', ' ', 'g')))
                 = TRIM(LOWER(REGEXP_REPLACE(s.utm_medium, '[-_\s]+', ' ', 'g')))),
            TRIM(LOWER(REGEXP_REPLACE(s.utm_medium, '[-_\s]+', ' ', 'g')))
        ) AS norm_ad,
        LOWER(TRIM(s.email)) AS email,
        RIGHT(TRIM(s.phone), 10) AS phone,
        0 AS is_icp
    FROM users_info s
    WHERE DATE(s.date_joined) >= (SELECT start_date FROM params)
      AND DATE(s.date_joined) <= (SELECT end_date FROM params)
      AND s.course_slug    = 'agentic-ai-generalist-course'
      AND s.marketing_slug = 'agentic-generalist-signup'
      AND LOWER(s.utm_source) IN ('facebook_ads', 'fb')
),
signup_counts AS (
    SELECT norm_ad, COUNT(DISTINCT email) AS signup_count
    FROM signups_raw
    GROUP BY 1
),
inbounds_raw AS (
    SELECT
        COALESCE(
            (SELECT TRIM(LOWER(REGEXP_REPLACE(am.correct_ad_name, '[-_\s]+', ' ', 'g')))
             FROM alias_map am
             WHERE TRIM(LOWER(REGEXP_REPLACE(am.utm_medium_variant, '[-_\s]+', ' ', 'g')))
                 = TRIM(LOWER(REGEXP_REPLACE(i.utm_medium, '[-_\s]+', ' ', 'g')))),
            TRIM(LOWER(REGEXP_REPLACE(i.utm_medium, '[-_\s]+', ' ', 'g')))
        ) AS norm_ad,
        LOWER(TRIM(i.email)) AS email,
        RIGHT(TRIM(i.phone_number), 10) AS phone,
        CASE
            WHEN REGEXP_REPLACE(i.expertise, '[^a-zA-Z0-9 /]', '', 'g') = 'Software Developers'
             AND i.years_of_work_experience != 'Not Working ( Student )'
             AND i.years_of_work_experience IS NOT NULL
            THEN 1 ELSE 0
        END AS is_icp
    FROM ds_inbound_form_response_v2 i
    WHERE (i.form_created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date
              >= (SELECT start_date FROM params)
      AND (i.form_created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date
              <= (SELECT end_date FROM params)
      AND LOWER(i.utm_source) IN ('facebook_ads', 'fb')
      AND (
            i.from_source IN (
                'hero_see_offerings_agentic','agentic_sde_db_hero','db_workflows',
                'see_offerings_v1','db_offerings_v2','start_ai_carrer',
                'all_projects_agentic_sde','start_learning_agentic_projects',
                'db_agentic_curriculum_v1','db_agentic_curriculum_v2',
                'connect_mentors_agentic_sde','get_callback_pricing',
                'db_pricing_section','career_change_callback',
                'course_details_bottom_sde','getcall_back_agentic_sde'
            )
         OR i.from_source IN (
                'download_brochure_agentic_v1','download_brochure_mk_agentic',
                'download_brochure_mk2_agentic','download_brochure_toolkit_agentic',
                'rcb_roles_agentic','download_brochure_cu1_agentic',
                'download_brochure_cu2_agentic','applynow_curriculum_agentic',
                'connect_mentors_agentic','gs_rcb_agentic','cn_rcb_agentic',
                'rcb_botton_agentic','nav_rcb_agentic','downlaod_brochure_capsule_agentic',
                'agentic_nav_course_details',
                'hero_apply_pm','brochure_agentic_v1_pm','nav_rcb_agentic_pm',
                'brochure_mk_agentic_pm','brochure_mk2_agentic_pm','brochure_toolkit_agentic_pm',
                'rcb_roles_agentic_pm','agentic_brochure_cu1_pm','applynow_curriculum_agentic_pm',
                'connect_mentors_agentic_pm','gs_rcb_agentic_pm','cn_rcb_agentic_pm',
                'agentic_nav_course_details_pm','agentic_nav_course_details_exp_pm',
                'agentic_brochure_mobile_pm','brochure_cu2_agentic_pm','brochure_capsule_agentic_pm'
            )
          )
),
inbound_counts AS (
    SELECT norm_ad, COUNT(DISTINCT email) AS inbound_count
    FROM inbounds_raw
    GROUP BY 1
),
apply_base AS (
    SELECT
        COALESCE(
            (SELECT TRIM(LOWER(REGEXP_REPLACE(am.correct_ad_name, '[-_\s]+', ' ', 'g')))
             FROM alias_map am
             WHERE TRIM(LOWER(REGEXP_REPLACE(am.utm_medium_variant, '[-_\s]+', ' ', 'g')))
                 = TRIM(LOWER(REGEXP_REPLACE(c.utm_medium, '[-_\s]+', ' ', 'g')))),
            TRIM(LOWER(REGEXP_REPLACE(c.utm_medium, '[-_\s]+', ' ', 'g')))
        ) AS norm_ad,
        LOWER(TRIM(c.email)) AS email,
        RIGHT(TRIM(c.phone), 10) AS phone,
        CASE
            WHEN c.area_of_expertise = 'Software Engineering'
             AND c.years_of_work_experience NOT IN (
                 '0 years (College Student)', '0 years (School Student)'
             )
             AND c.years_of_work_experience IS NOT NULL
             AND COALESCE(c.best_describes_your_current_role, '') != 'Student'
            THEN 1 ELSE 0
        END AS is_icp
    FROM course_x_user_info c
    WHERE (COALESCE(
               c.course_user_apply_form_mapping_created_at,
               c.source_last_updated_at
           ) AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date
          BETWEEN (SELECT start_date FROM params) AND (SELECT end_date FROM params)
      AND c.coursestructure_slug IN ('agentic-ai-course', 'agentic-ai-generalist-course')
      AND LOWER(c.utm_source) IN ('facebook_ads', 'fb')
),
applyform_metrics AS (
    SELECT norm_ad, COUNT(DISTINCT email) AS total_applyform_fills
    FROM apply_base
    GROUP BY norm_ad
),
lead_captured_raw AS (
    SELECT norm_ad, email FROM signups_raw
    UNION
    SELECT norm_ad, email FROM inbounds_raw
    UNION
    SELECT norm_ad, email FROM apply_base
),
lead_captured_counts AS (
    SELECT norm_ad, COUNT(DISTINCT email) AS lead_captured_count
    FROM lead_captured_raw
    GROUP BY 1
),
lead_generated_counts AS (
    SELECT
        COALESCE(ic.norm_ad, af.norm_ad) AS norm_ad,
        COALESCE(ic.inbound_count, 0) + COALESCE(af.total_applyform_fills, 0) AS lead_generated
    FROM inbound_counts ic
    FULL OUTER JOIN applyform_metrics af ON af.norm_ad = ic.norm_ad
),
icp_touch_raw AS (
    SELECT norm_ad, email, phone, is_icp FROM apply_base
    UNION ALL
    SELECT norm_ad, email, phone, is_icp FROM inbounds_raw
    UNION ALL
    SELECT norm_ad, email, phone, is_icp FROM signups_raw
),
icp_deduped AS (
    SELECT
        norm_ad,
        COALESCE(email, phone) AS identity_key,
        MAX(is_icp) AS is_icp
    FROM icp_touch_raw
    WHERE email IS NOT NULL OR phone IS NOT NULL
    GROUP BY norm_ad, COALESCE(email, phone)
),
sde_icp_counts AS (
    SELECT norm_ad, COALESCE(SUM(is_icp), 0) AS sde_icp_count
    FROM icp_deduped
    GROUP BY norm_ad
),
rfd_params AS (
    SELECT
        EXTRACT(DAY FROM (SELECT start_date FROM params))::int AS from_day,
        EXTRACT(DAY FROM (SELECT end_date   FROM params))::int AS to_day
),
latest_enrollment AS (
    SELECT prospect_email, mx_course_enrolled, mx_enrolled_on_date,
           alt_email, phone, lead_created_on
    FROM (
        SELECT
            LOWER(TRIM(REGEXP_REPLACE(
                prospect_email, '[[:space:]‌‍﻿]', '', 'g'
            )))                     AS prospect_email,
            mx_course_enrolled,
            mx_enrolled_on_date,
            LOWER(TRIM(REGEXP_REPLACE(
                mx_alternate_email_id, '[[:space:]‌‍﻿]', '', 'g'
            )))                     AS alt_email,
            RIGHT(REGEXP_REPLACE(
                TRIM(REGEXP_REPLACE(COALESCE(phone, ''), '[[:space:]‌‍﻿]', '', 'g')),
            '[^0-9]', '', 'g'), 10) AS phone,
            lead_created_on,
            ROW_NUMBER() OVER (
                PARTITION BY LOWER(TRIM(REGEXP_REPLACE(
                    prospect_email, '[[:space:]‌‍﻿]', '', 'g'
                )))
                ORDER BY modified_on DESC
            ) AS rn
        FROM lsq_leads_x_activities_v2
        WHERE prospect_id IS NOT NULL
          AND mx_enrolled_on_date IS NOT NULL
          AND mx_enrolled_on_date::date >= DATE_TRUNC('month', (SELECT start_date FROM params))
          AND mx_enrolled_on_date::date <  DATE_TRUNC('month', (SELECT start_date FROM params)) + INTERVAL '1 month'
    ) ranked
    WHERE rn = 1
      AND mx_course_enrolled IN ('Agentic AI SDE', 'Agentic AI Generalist')
),
perf_all_filtered AS (
    SELECT norm_ad, email AS prospect_email, phone
    FROM signups_raw
    UNION ALL
    SELECT norm_ad, email, phone
    FROM inbounds_raw
    UNION ALL
    SELECT norm_ad, email, phone
    FROM apply_base
),
rfd_touchpoints AS (
    SELECT
        lv.prospect_email, lv.mx_course_enrolled,
        lv.mx_enrolled_on_date::date AS sale_date,
        lv.lead_created_on,
        p.norm_ad
    FROM latest_enrollment lv
    JOIN rfd_params rp ON true
    JOIN perf_all_filtered p ON p.prospect_email = lv.prospect_email
    WHERE EXTRACT(DAY FROM lv.mx_enrolled_on_date::date) >= rp.from_day
      AND EXTRACT(DAY FROM lv.mx_enrolled_on_date::date) <= rp.to_day
    UNION ALL
    SELECT
        lv.prospect_email, lv.mx_course_enrolled,
        lv.mx_enrolled_on_date::date AS sale_date,
        lv.lead_created_on,
        p.norm_ad
    FROM latest_enrollment lv
    JOIN rfd_params rp ON true
    JOIN perf_all_filtered p
      ON p.prospect_email = lv.alt_email
     AND lv.alt_email IS NOT NULL AND lv.alt_email != ''
     AND p.prospect_email != lv.prospect_email
    WHERE EXTRACT(DAY FROM lv.mx_enrolled_on_date::date) >= rp.from_day
      AND EXTRACT(DAY FROM lv.mx_enrolled_on_date::date) <= rp.to_day
    UNION ALL
    SELECT
        lv.prospect_email, lv.mx_course_enrolled,
        lv.mx_enrolled_on_date::date AS sale_date,
        lv.lead_created_on,
        p.norm_ad
    FROM latest_enrollment lv
    JOIN rfd_params rp ON true
    JOIN perf_all_filtered p
      ON p.phone = lv.phone
     AND lv.phone IS NOT NULL AND lv.phone != ''
     AND p.phone IS NOT NULL AND p.phone != ''
     AND (p.prospect_email IS DISTINCT FROM lv.prospect_email)
     AND (p.prospect_email IS DISTINCT FROM lv.alt_email)
    WHERE EXTRACT(DAY FROM lv.mx_enrolled_on_date::date) >= rp.from_day
      AND EXTRACT(DAY FROM lv.mx_enrolled_on_date::date) <= rp.to_day
),
rfd_winner AS (
    SELECT DISTINCT ON (prospect_email)
        prospect_email, mx_course_enrolled, sale_date, lead_created_on, norm_ad
    FROM rfd_touchpoints
    ORDER BY prospect_email, sale_date ASC
),
rfd_m0_by_ad AS (
    SELECT
        norm_ad,
        COUNT(DISTINCT CASE WHEN mx_course_enrolled = 'Agentic AI SDE'       THEN prospect_email END) AS m0_sde_rfd,
        COUNT(DISTINCT CASE WHEN mx_course_enrolled = 'Agentic AI Generalist' THEN prospect_email END) AS m0_gen_rfd
    FROM rfd_winner
    WHERE DATE_TRUNC('month', lead_created_on::date) = DATE_TRUNC('month', sale_date)
    GROUP BY 1
),
rfd_m1_by_ad AS (
    SELECT
        norm_ad,
        COUNT(DISTINCT CASE WHEN mx_course_enrolled = 'Agentic AI SDE'       THEN prospect_email END) AS m1_sde_rfd,
        COUNT(DISTINCT CASE WHEN mx_course_enrolled = 'Agentic AI Generalist' THEN prospect_email END) AS m1_gen_rfd
    FROM rfd_winner
    WHERE DATE_TRUNC('month', lead_created_on::date) = DATE_TRUNC('month', sale_date) - INTERVAL '1 month'
    GROUP BY 1
),
activity_window AS (
    SELECT *
    FROM lsq_leads_x_activities_v2
    WHERE modified_on::date BETWEEN (SELECT start_date FROM params)
                                AND (SELECT end_date FROM params)
      AND prospect_id IS NOT NULL
),
lead_assignments AS (
    SELECT
        prospect_id,
        LOWER(TRIM(lead_owner)) AS lead_owner,
        MIN(modified_on)        AS assignment_ts
    FROM activity_window
    WHERE event IN ('LeadAssigned', 'StageChange')
      AND prospect_stage = 'Lead'
    GROUP BY prospect_id, LOWER(TRIM(lead_owner))
),
mapped_assignments AS (
    SELECT
        la.prospect_id,
        la.lead_owner,
        la.assignment_ts,
        ROW_NUMBER() OVER (
            PARTITION BY la.prospect_id
            ORDER BY u.effective_from DESC
        ) AS rn
    FROM lead_assignments la
    JOIN {{#9601-bde-x-course-x-active-periods-since-jan25}} u
      ON LOWER(TRIM(u.lead_owner)) = la.lead_owner
     AND la.assignment_ts     >= u.effective_from::date
     AND (u.effective_to IS NULL OR la.assignment_ts <= u.effective_to::date)
     AND u.user_role            = 'Sales'
     AND u.course               = 'Agentic AI'
),
valid_assigned_prospects AS (
    SELECT DISTINCT prospect_id
    FROM mapped_assignments
    WHERE rn = 1
),
prospect_details AS (
    SELECT DISTINCT ON (a.prospect_id)
        a.prospect_id,
        LOWER(TRIM(a.prospect_email)) AS prospect_email,
        LOWER(TRIM(a.mx_alternate_email_id)) AS alt_email,
        RIGHT(TRIM(COALESCE(a.phone::text, '')), 10) AS lsq_phone
    FROM activity_window a
    JOIN mapped_assignments ma ON ma.prospect_id = a.prospect_id AND ma.rn = 1
    ORDER BY a.prospect_id, a.modified_on ASC
),
perf_touch_emails AS (
    SELECT norm_ad, email AS touch_email FROM signups_raw WHERE email IS NOT NULL
    UNION
    SELECT norm_ad, email AS touch_email FROM inbounds_raw WHERE email IS NOT NULL
    UNION
    SELECT norm_ad, email AS touch_email FROM apply_base WHERE email IS NOT NULL
),
perf_touch_phones AS (
    SELECT norm_ad, phone AS touch_phone FROM signups_raw WHERE phone IS NOT NULL AND phone != ''
    UNION
    SELECT norm_ad, phone AS touch_phone FROM inbounds_raw WHERE phone IS NOT NULL AND phone != ''
    UNION
    SELECT norm_ad, phone AS touch_phone FROM apply_base WHERE phone IS NOT NULL AND phone != ''
),
perf_flag AS (
    SELECT DISTINCT pd.prospect_id, pt.norm_ad
    FROM prospect_details pd
    JOIN perf_touch_emails pt
      ON pt.touch_email = pd.prospect_email
      OR (pd.alt_email IS NOT NULL AND pd.alt_email != '' AND pt.touch_email = pd.alt_email)
    UNION
    SELECT DISTINCT pd.prospect_id, pp.norm_ad
    FROM prospect_details pd
    JOIN perf_touch_phones pp
      ON pd.lsq_phone IS NOT NULL AND pd.lsq_phone != ''
     AND pp.touch_phone = pd.lsq_phone
),
assigned_leads AS (
    SELECT
        pf.norm_ad,
        COUNT(DISTINCT pf.prospect_id) AS assigned_count
    FROM perf_flag pf
    INNER JOIN valid_assigned_prospects vap ON vap.prospect_id = pf.prospect_id
    GROUP BY 1
),
stage_flags AS (
    SELECT
        prospect_id,
        MAX(CASE WHEN current_stage = 'Prospect'          THEN 1 ELSE 0 END) AS is_prospect,
        MAX(CASE WHEN current_stage = 'Session Scheduled' THEN 1 ELSE 0 END) AS is_session_scheduled,
        MAX(CASE WHEN current_stage = 'Session Done'      THEN 1 ELSE 0 END) AS is_session_done
    FROM activity_window
    WHERE event = 'StageChange'
    GROUP BY prospect_id
),
stage_by_ad AS (
    SELECT
        pf.norm_ad,
        COUNT(DISTINCT CASE WHEN sf.is_prospect = 1          THEN pf.prospect_id END) AS prospect_count,
        COUNT(DISTINCT CASE WHEN sf.is_session_scheduled = 1 THEN pf.prospect_id END) AS session_scheduled_count,
        COUNT(DISTINCT CASE WHEN sf.is_session_done = 1      THEN pf.prospect_id END) AS session_done_count
    FROM perf_flag pf
    INNER JOIN valid_assigned_prospects vap ON vap.prospect_id = pf.prospect_id
    LEFT JOIN stage_flags sf ON sf.prospect_id = pf.prospect_id
    GROUP BY 1
),
base_result AS (
    SELECT
        a.ad_id,
        a.ad_name,
        a.ad_group_id,
        a.ad_group_name,
        a.campaign_id,
        a.campaign_name,
        a.platform,
        a.impressions,
        a.link_clicks,
        a.spend,
        COALESCE(sc.signup_count, 0)          AS signups,
        COALESCE(ic.inbound_count, 0)         AS inbounds,
        COALESCE(af.total_applyform_fills, 0) AS applyform_fills,
        COALESCE(lc.lead_captured_count, 0)   AS lead_captured,
        COALESCE(lg.lead_generated, 0)        AS lead_generated,
        COALESCE(sic.sde_icp_count, 0)        AS sde_icp,
        COALESCE(al.assigned_count, 0)        AS assigned_leads,
        COALESCE(sba.prospect_count, 0)          AS prospect_count,
        COALESCE(sba.session_scheduled_count, 0) AS session_scheduled_count,
        COALESCE(sba.session_done_count, 0)      AS session_done_count,
        COALESCE(rfd0.m0_sde_rfd, 0)          AS m0_sde_rfd,
        COALESCE(rfd0.m0_gen_rfd, 0)          AS m0_gen_rfd,
        COALESCE(rfd1.m1_sde_rfd, 0)          AS m1_sde_rfd,
        COALESCE(rfd1.m1_gen_rfd, 0)          AS m1_gen_rfd
    FROM ad_metrics a
    LEFT JOIN signup_counts        sc   ON sc.norm_ad   = a.norm_ad
    LEFT JOIN inbound_counts       ic   ON ic.norm_ad   = a.norm_ad
    LEFT JOIN applyform_metrics    af   ON af.norm_ad   = a.norm_ad
    LEFT JOIN lead_captured_counts lc   ON lc.norm_ad   = a.norm_ad
    LEFT JOIN lead_generated_counts lg  ON lg.norm_ad   = a.norm_ad
    LEFT JOIN sde_icp_counts       sic  ON sic.norm_ad  = a.norm_ad
    LEFT JOIN assigned_leads       al   ON al.norm_ad   = a.norm_ad
    LEFT JOIN stage_by_ad          sba  ON sba.norm_ad  = a.norm_ad
    LEFT JOIN rfd_m0_by_ad         rfd0 ON rfd0.norm_ad = a.norm_ad
    LEFT JOIN rfd_m1_by_ad         rfd1 ON rfd1.norm_ad = a.norm_ad
)
SELECT * FROM (
SELECT
    ad_id                                                                       AS "Ad ID",
    ad_name                                                                     AS "Ad Name",
    ad_group_name                                                               AS "Ad Group Name",
    campaign_name                                                               AS "Campaign Name",
    spend                                                                       AS "Ad Spend",
    impressions                                                                 AS "Impressions",
    link_clicks                                                                 AS "Link Clicks",
    ROUND(link_clicks * 100.0 / NULLIF(impressions, 0), 2) || '%'               AS "CTR%",
    ROUND(spend * 1000.0      / NULLIF(impressions, 0), 2)                      AS "CPM",
    ROUND(spend               / NULLIF(link_clicks, 0), 2)                      AS "CPLC",
    inbounds                                                                    AS "Inbound Requests",
    applyform_fills                                                             AS "Apply Form Fills",
    lead_captured                                                               AS "Leads Captured",
    ROUND(spend / NULLIF(lead_captured, 0), 0)                                  AS "CPLC (Lead Captured)",
    lead_generated                                                              AS "Leads Generated",
    ROUND(spend / NULLIF(lead_generated, 0), 0)                                 AS "CPLG",
    sde_icp                                                                     AS "SDE ICP",
    ROUND(spend / NULLIF(sde_icp, 0), 0)                                        AS "Cost per SDE ICP (Rs)",
    assigned_leads                                                              AS "Assigned Leads (BDE + SCD)",
    ROUND(spend / NULLIF(assigned_leads, 0), 0)                                 AS "CPLA",
    prospect_count                                                              AS "Prospect",
    session_scheduled_count                                                     AS "SC",
    session_done_count                                                         AS "SD",
    m0_sde_rfd                                                                  AS "M0 RFD (SDE)",
    m1_sde_rfd                                                                  AS "M-1 RFD (SDE)",
    m0_gen_rfd                                                                  AS "M0 RFD (Generalist)",
    m1_gen_rfd                                                                  AS "M-1 RFD (Generalist)",
    (m0_sde_rfd + m1_sde_rfd + m0_gen_rfd + m1_gen_rfd)                         AS "Total RFD (All)",
    ROUND(spend / NULLIF(m0_sde_rfd + m1_sde_rfd + m0_gen_rfd + m1_gen_rfd, 0), 0) AS "CPRFD",
    1 AS sort_order
FROM base_result
UNION ALL
SELECT
    'TOTAL', 'TOTAL', 'TOTAL', 'TOTAL',
    SUM(spend),
    SUM(impressions),
    SUM(link_clicks),
    ROUND(SUM(link_clicks) * 100.0 / NULLIF(SUM(impressions), 0), 2) || '%',
    ROUND(SUM(spend) * 1000.0      / NULLIF(SUM(impressions), 0), 2),
    ROUND(SUM(spend)               / NULLIF(SUM(link_clicks), 0), 2),
    SUM(inbounds),
    SUM(applyform_fills),
    SUM(lead_captured),
    ROUND(SUM(spend) / NULLIF(SUM(lead_captured), 0), 0),
    SUM(lead_generated),
    ROUND(SUM(spend) / NULLIF(SUM(lead_generated), 0), 0),
    SUM(sde_icp),
    ROUND(SUM(spend) / NULLIF(SUM(sde_icp), 0), 0),
    SUM(assigned_leads),
    ROUND(SUM(spend) / NULLIF(SUM(assigned_leads), 0), 0),
    SUM(prospect_count),
    SUM(session_scheduled_count),
    SUM(session_done_count),
    SUM(m0_sde_rfd),
    SUM(m1_sde_rfd),
    SUM(m0_gen_rfd),
    SUM(m1_gen_rfd),
    SUM(m0_sde_rfd + m1_sde_rfd + m0_gen_rfd + m1_gen_rfd),
    ROUND(SUM(spend) / NULLIF(SUM(m0_sde_rfd + m1_sde_rfd + m0_gen_rfd + m1_gen_rfd), 0), 0),
    2 AS sort_order
FROM base_result
) final_output
ORDER BY sort_order, "Campaign Name", "Ad Name";
"""


def get_worksheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet(SHEET_TAB_NAME)


def read_filters(ws):
    month_raw = ws.acell("B1").value
    year_raw = ws.acell("B2").value
    start_raw = ws.acell("B3").value
    end_raw = ws.acell("B4").value

    if not all([month_raw, year_raw, start_raw, end_raw]):
        sys.exit("ERROR: Month/Year/Start Date/End Date cells (B1:B4) are not all filled in.")

    month_num = MONTHS.get(month_raw.strip().lower())
    if not month_num:
        sys.exit(f"ERROR: '{month_raw}' is not a recognized month name (e.g. September).")

    year = int(year_raw)
    start_day = int(start_raw)
    end_day = int(end_raw)

    from_date = dt.date(year, month_num, start_day)
    to_date = dt.date(year, month_num, end_day)
    return from_date, to_date


def run_metabase_query(from_date, to_date):
    body = {
        "database": MB_DATABASE_ID,
        "type": "native",
        "native": {
            "query": SQL_QUERY,
            "template-tags": {
                "from_date": {
                    "id": "fc8f19af-a808-41b3-831f-bbd80335f35c",
                    "name": "from_date",
                    "display-name": "From Date",
                    "type": "date",
                },
                "to_date": {
                    "id": "e796c2a4-f3f0-4b6b-88a4-920337327e09",
                    "name": "to_date",
                    "display-name": "To Date",
                    "type": "date",
                },
                "#9601-bde-x-course-x-active-periods-since-jan25": {
                    "id": "522554ad-2e9f-4b2e-bc14-41edb030be9a",
                    "name": "#9601-bde-x-course-x-active-periods-since-jan25",
                    "display-name": "#9601 Bde X Course X Active Periods Since Jan25",
                    "type": "card",
                    "card-id": 9601,
                },
            },
        },
        "parameters": [
            {
                "type": "date/single",
                "target": ["variable", ["template-tag", "from_date"]],
                "value": from_date.isoformat(),
            },
            {
                "type": "date/single",
                "target": ["variable", ["template-tag", "to_date"]],
                "value": to_date.isoformat(),
            },
        ],
    }

    r = requests.post(
        f"{MB_BASE_URL}/api/dataset",
        headers={"x-api-key": MB_API_KEY, "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    if not r.ok:
        print(f"Metabase returned {r.status_code}. Response body:\n{r.text}")
    r.raise_for_status()
    payload = r.json()

    data = payload["data"]
    cols = [c["display_name"] or c["name"] for c in data["cols"]]
    rows = data["rows"]
    return cols, rows


def write_to_sheet(ws, from_date, to_date, cols, rows):
    drop_idx = [i for i, c in enumerate(cols) if c.strip().lower() == "sort_order"]

    def clean_row(row):
        return [v for i, v in enumerate(row) if i not in drop_idx]

    clean_cols = clean_row(cols)
    clean_rows = [clean_row(r) for r in rows]

    n_cols = len(clean_cols)
    n_rows = len(clean_rows)

    ws.batch_clear([f"A{HEADER_ROW}:Z{DATA_START_ROW + 500}"])

    values = [clean_cols] + clean_rows
    end_col_letter = gspread.utils.rowcol_to_a1(1, n_cols).rstrip("1")
    rng = f"A{HEADER_ROW}:{end_col_letter}{HEADER_ROW + n_rows}"
    ws.update(rng, values, value_input_option="USER_ENTERED")

    total_row_num = HEADER_ROW + n_rows

    fmt_requests = {"requests": []}
    sheet_id = ws.id

    def add_fmt(row_start, row_end, col_start, col_end, fmt, fields):
        fmt_requests["requests"].append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_start - 1,
                    "endRowIndex": row_end,
                    "startColumnIndex": col_start - 1,
                    "endColumnIndex": col_end,
                },
                "cell": {"userEnteredFormat": fmt},
                "fields": fields,
            }
        })

    add_fmt(
        HEADER_ROW, HEADER_ROW, 1, n_cols,
        {
            "backgroundColor": {"red": 0.11, "green": 0.15, "blue": 0.25},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            "horizontalAlignment": "CENTER",
            "wrapStrategy": "WRAP",
        },
        "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,wrapStrategy)",
    )

    add_fmt(
        total_row_num, total_row_num, 1, n_cols,
        {
            "backgroundColor": {"red": 0.93, "green": 0.95, "blue": 1.0},
            "textFormat": {"bold": True},
        },
        "userEnteredFormat(backgroundColor,textFormat)",
    )

    fmt_requests["requests"].append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 4,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(textFormat)",
        }
    })

    fmt_requests["requests"].append({
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": HEADER_ROW}},
            "fields": "gridProperties.frozenRowCount",
        }
    })

    fmt_requests["requests"].append({
        "autoResizeDimensions": {
            "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": n_cols}
        }
    })

    ws.spreadsheet.batch_update(fmt_requests)

    ws.update_acell("D1", f"Last synced: {dt.datetime.utcnow().isoformat(timespec='seconds')} UTC "
                          f"(range used: {from_date} to {to_date})")


def main():
    ws = get_worksheet()
    from_date, to_date = read_filters(ws)
    print(f"Running query for {from_date} -> {to_date} ...")
    cols, rows = run_metabase_query(from_date, to_date)
    print(f"Got {len(rows)} rows back. Writing to sheet...")
    write_to_sheet(ws, from_date, to_date, cols, rows)
    print("Done.")


if __name__ == "__main__":
    main()
