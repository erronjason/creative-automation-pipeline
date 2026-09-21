"""Prompt construction. Deterministic templates, recorded verbatim in the manifest."""

from __future__ import annotations

from .brand import Brand
from .brief import Brief, Product

NO_TEXT = "No text, letters, numbers, logos, labels with words, or watermarks anywhere in the image."


def hero_prompt(brief: Brief, product: Product, brand: Brand) -> str:
    if product.prompt:
        return f"{product.prompt.strip()} {NO_TEXT}"
    scene = product.scene or brief.campaign.visual_direction or "clean studio setting"
    return (
        f"Commercial advertising photograph of {product.name}: {product.description}. "
        f"Scene: {scene}. "
        f"Made for a social campaign aimed at {brief.target.audience} in {brief.target.region}. "
        f"Art direction: {brand.visual_style}. "
        "Composition: the product is the clear hero, centered, fully in frame, with generous empty "
        "background around it so the image can be reframed and have copy placed over the lower third. "
        f"{NO_TEXT}"
    )


def expand_prompt(brief: Brief, product: Product, brand: Brand) -> str:
    scene = product.scene or brief.campaign.visual_direction or "the same setting"
    return (
        f"Seamlessly extend the existing photograph outward. Continue the background and lighting of {scene}. "
        f"Do not add new products or people. Keep the style: {brand.visual_style}. {NO_TEXT}"
    )
