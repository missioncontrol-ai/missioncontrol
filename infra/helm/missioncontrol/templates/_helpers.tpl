{{- define "missioncontrol.name" -}}
{{- default "missioncontrol" .Values.nameOverride -}}
{{- end -}}

{{- define "missioncontrol.fullname" -}}
{{- printf "%s-%s" (include "missioncontrol.name" .) .Release.Namespace | trunc 63 | trimSuffix "-" -}}
{{- end -}}
