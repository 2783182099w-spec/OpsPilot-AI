<template>
  <header class="workspace-heading">
    <div>
      <p class="ops-eyebrow">{{ section }}</p>
      <h1 class="ops-heading">{{ title }}</h1>
      <p v-if="description" class="ops-muted description">{{ description }}</p>
    </div>
    <slot/>
  </header>
</template>

<script setup>
defineProps({ section:String, title:String, description:String })
</script>

<style scoped>
.workspace-heading { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; margin:3px 0 17px; }
.description { margin:7px 0 0; }
</style>
