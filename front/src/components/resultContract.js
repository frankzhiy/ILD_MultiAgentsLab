// Current review pages require the diagnostic pipeline's full saved contract.
export const isCurrentTeam = team => Boolean(team?.schema_version === 'team_synthesis.v2'
  && team.diagnostic_opinion && team.diagnostic_exploration
  && ['specific_diagnosis', 'provisional_diagnosis', 'specific_differential', 'needs_discussion'].includes(team.diagnostic_resolution?.status))

export const isCurrentSpecialty = output => Boolean(output
  && Object.keys(output).length === 2 && output.interspecialty_questions
  && output.specialty_assessments?.assessments?.length
  && output.specialty_assessments.assessments.every(item => item.conditions
    && ['assessability', 'direction', 'confidence', 'clinical_role'].every(key => item[key])
    && item.claims?.length && item.claims.every(claim => claim.claim_id && Array.isArray(claim.evidence_relations))))
