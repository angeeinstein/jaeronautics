/**
 * My Account › Contact details: address, phones and both email addresses,
 * saved at once without review. A new private address is the login and waits
 * to be confirmed; so does a new university address. The rules are the
 * server's (forms.py): a student's university address, for one, stays one.
 */
import { Button, Group, Select, SimpleGrid, Stack, TextInput } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { api, ApiError, call, type Schemas } from '../../api/client';
import { Panel } from '../../components/Panel';
import { emptyToNull } from '../../lib/forms';
import { notifyFailed } from '../../lib/notify';
import { type FormOptions, useTakeSaved } from './shared';

type Contact = Schemas['ContactOut'];
type ContactIn = Schemas['ContactIn'];

function initial(contact: Contact): ContactIn {
  return {
    street: contact.street ?? '',
    house_number: contact.house_number ?? '',
    postal_code: contact.postal_code ?? '',
    city: contact.city ?? '',
    country: contact.country ?? '',
    phone_private: contact.phone_private ?? '',
    email_private: contact.email_private ?? '',
    phone_work: contact.phone_work ?? '',
    email_work: contact.email_work ?? '',
  };
}

export function ContactCard({ contact, options }: { contact: Contact; options: FormOptions }) {
  const [value, setValue] = useState(() => initial(contact));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const take = useTakeSaved();
  const save = useMutation({
    mutationFn: () =>
      call(
        api.PUT('/api/v1/account/contact', {
          body: {
            ...value,
            phone_work: emptyToNull(value.phone_work),
            email_work: emptyToNull(value.email_work),
          },
        }),
      ),
    onSuccess: (saved) => {
      setErrors({});
      take(saved);
    },
    onError: (error) => {
      if (error instanceof ApiError && Object.keys(error.fields).length) setErrors(error.fields);
      else notifyFailed(error);
    },
  });
  const field = (key: keyof ContactIn) => ({
    value: value[key] ?? '',
    error: errors[key] ?? null,
    onChange: (event: React.ChangeEvent<HTMLInputElement>) => {
      setValue({ ...value, [key]: event.currentTarget.value });
      if (errors[key]) setErrors({ ...errors, [key]: '' });
    },
  });

  return (
    <Panel title="Contact details">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <TextInput label="Street" autoComplete="address-line1" maxLength={255} {...field('street')} />
            <TextInput label="House number" maxLength={20} {...field('house_number')} />
            <TextInput
              label="Postal code"
              autoComplete="postal-code"
              maxLength={20}
              {...field('postal_code')}
            />
            <TextInput label="City" autoComplete="address-level2" maxLength={100} {...field('city')} />
            <Select
              label="Country"
              data={options.countries}
              searchable
              value={value.country || null}
              error={errors.country ?? null}
              onChange={(country) => {
                setValue({ ...value, country: country ?? '' });
              }}
            />
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
            <TextInput
              label="Private email"
              description="Your login. A new one waits to be confirmed."
              type="email"
              autoComplete="email"
              maxLength={255}
              {...field('email_private')}
            />
            <TextInput
              label="Private phone"
              type="tel"
              autoComplete="tel"
              maxLength={50}
              {...field('phone_private')}
            />
            <TextInput
              label="University or company email"
              type="email"
              maxLength={255}
              {...field('email_work')}
            />
            <TextInput label="Work phone" type="tel" maxLength={50} {...field('phone_work')} />
          </SimpleGrid>
          <Group justify="flex-end">
            <Button type="submit" loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </form>
    </Panel>
  );
}
